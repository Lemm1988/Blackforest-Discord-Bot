/**
 * Wiederverwendbarer Discord-Embed-Builder.
 *
 * Wird sowohl auf der "Embed senden"-Seite (create_embed.html) als auch im
 * Embed-Modus der "DM senden"-Seite (send_dm.html) verwendet - beide binden
 * dasselbe Markup (Makro `embed_builder` in _embed_builder.html) ein und
 * rufen `initEmbedBuilder(prefix)` mit ihrem jeweiligen ID-Prefix auf, damit
 * mehrere Instanzen auf derselben Seite nie kollidieren.
 *
 * Die Zeichen-/Anzahl-Limits hier entsprechen exakt der offiziellen Discord-
 * Dokumentation (https://discord.com/developers/docs/resources/message#embed-object-embed-limits):
 *   title <= 256, description <= 4096, fields <= 25, field.name <= 256,
 *   field.value <= 1024, footer.text <= 2048, author.name <= 256,
 *   Summe aus title+description+alle field.name/value+footer.text+author.name <= 6000.
 * Das Backend (`_build_embed_from_payload` in app.py) erzwingt dieselben
 * Limits serverseitig noch einmal - diese Anzeige hier ist nur Komfort,
 * keine Sicherheitsgrenze.
 */
(function (window, document) {
    const LIMITS = {
        title: 256,
        description: 4096,
        fieldName: 256,
        fieldValue: 1024,
        footer: 2048,
        authorName: 256,
        fieldsMax: 25,
        total: 6000,
    };

    const COLOR_PRESETS = [
        ['#2f7a4d', 'Blackforest-Grün'],
        ['#5865f2', 'Discord-Blurple'],
        ['#57f287', 'Grün'],
        ['#fee75c', 'Gelb'],
        ['#eb459e', 'Pink'],
        ['#ed4245', 'Rot'],
        ['#e67e22', 'Orange'],
        ['#95a5a6', 'Grau'],
        ['#23272a', 'Fast Schwarz'],
    ];

    const EMOJIS = ['😀', '😎', '😂', '😍', '😢', '😇', '😨', '😉', '🤯', '👽', '🤩', '🥺', '🔥', '🎉', '🌿', '❤️', '💙', '👍', '👎', '🌽', '⭐', '💯', '🤔', '💥', '💫', '🌟', '🎶', '🍕', '🍔', '🍎', '🍩', '☕', '🌈', '🍀', '🌳', '🌻', '💤', '🎨', '🛸', '🚀', '🐜', '👀', '☣', '🧬', '❓', '❗', '✅', '☑', '🐞', '🌴'];

    function escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    // Sehr reduzierter, aber sicherer (erst escapen, dann Tags einfügen) Discord-
    // Markdown-Renderer für die Vorschau - deckt die in der Toolbar angebotenen
    // Formate ab (fett/kursiv/unterstrichen/durchgestrichen/Code/Codeblock/Links/
    // Spoiler) plus Zeilenumbrüche. Kein Anspruch auf 1:1-Discord-Parity, nur
    // eine plausible Annäherung für die Vorschau.
    function renderMarkdown(text) {
        if (!text) return '';
        let html = escapeHtml(text);
        html = html.replace(/```([\s\S]*?)```/g, (_m, code) => `<pre class="disc-code-block"><code>${code}</code></pre>`);
        html = html.replace(/`([^`\n]+)`/g, '<code class="disc-inline-code">$1</code>');
        html = html.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/__([^_]+)__/g, '<u>$1</u>');
        html = html.replace(/~~([^~]+)~~/g, '<s>$1</s>');
        html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        html = html.replace(/\|\|([\s\S]+?)\|\|/g, '<span class="disc-spoiler">$1</span>');
        html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
        html = html.replace(/\n/g, '<br>');
        return html;
    }

    function formatTimestamp(date) {
        return date.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    function initEmbedBuilder(prefix, options) {
        options = options || {};
        const el = (id) => document.getElementById(`${prefix}_${id}`);
        const root = document;

        const fieldsList = el('fields');
        const fieldCountLabel = el('fieldCount');
        const previewContent = el('previewContent');
        const totalCounter = el('totalCounter');

        function bindCounter(inputId, max) {
            const input = el(inputId);
            const counter = el(`${inputId}Counter`);
            if (!input || !counter) return;
            const update = () => {
                counter.textContent = `${input.value.length} / ${max}`;
                counter.classList.toggle('text-danger', input.value.length > max);
            };
            input.addEventListener('input', update);
            update();
        }
        bindCounter('title', LIMITS.title);
        bindCounter('description', LIMITS.description);
        bindCounter('footer', LIMITS.footer);
        bindCounter('authorName', LIMITS.authorName);

        // --- Farbe: Color-Picker <-> Hex-Textfeld <-> Presets <-> Zufallsfarbe,
        // alle vier bleiben immer synchron ---
        const colorPicker = el('color');
        const colorHex = el('colorHex');
        const swatchContainer = colorPicker ? colorPicker.closest('div').parentElement.querySelector('.eb-swatches') : null;

        function setColor(hex) {
            hex = hex.startsWith('#') ? hex : `#${hex}`;
            if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
            colorPicker.value = hex.toLowerCase();
            colorHex.value = hex.toUpperCase();
            updatePreview();
        }
        if (colorPicker && colorHex) {
            colorPicker.addEventListener('input', () => setColor(colorPicker.value));
            colorHex.addEventListener('input', () => {
                const v = colorHex.value.trim();
                if (/^#?[0-9a-fA-F]{6}$/.test(v)) setColor(v);
                else updatePreview();
            });
        }
        if (swatchContainer) {
            COLOR_PRESETS.forEach(([hex, label]) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'eb-swatch';
                btn.style.backgroundColor = hex;
                btn.title = label;
                btn.addEventListener('click', () => setColor(hex));
                swatchContainer.appendChild(btn);
            });
            const randomBtn = document.createElement('button');
            randomBtn.type = 'button';
            randomBtn.className = 'btn btn-sm btn-outline-secondary ms-1';
            randomBtn.textContent = '🎲';
            randomBtn.title = 'Zufällige Farbe';
            randomBtn.addEventListener('click', () => {
                const hex = `#${Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, '0')}`;
                setColor(hex);
            });
            swatchContainer.appendChild(randomBtn);
        }

        // --- Toolbar (fett/kursiv/... + Link) wirkt auf das per data-target
        // referenzierte Textfeld, an der aktuellen Cursorposition ---
        root.querySelectorAll(`[data-target="${prefix}_description"]`).forEach((btn) => {
            if (btn.dataset.format) {
                btn.addEventListener('click', () => {
                    const format = btn.dataset.format;
                    const textarea = el('description');
                    const start = textarea.selectionStart;
                    const end = textarea.selectionEnd;
                    const selected = textarea.value.slice(start, end);
                    textarea.value = textarea.value.slice(0, start) + format + selected + format + textarea.value.slice(end);
                    textarea.focus();
                    textarea.setSelectionRange(start + format.length, end + format.length);
                    textarea.dispatchEvent(new Event('input'));
                });
            }
        });
        const addLinkBtn = root.querySelector(`.eb-add-link[data-target="${prefix}_description"]`);
        if (addLinkBtn) {
            addLinkBtn.addEventListener('click', () => {
                const url = prompt('URL eingeben:');
                if (url) {
                    const textarea = el('description');
                    textarea.value += `[Link Text](${url})`;
                    textarea.dispatchEvent(new Event('input'));
                }
            });
        }

        // --- Emoji-Picker ---
        const emojiList = el('emojiList');
        if (emojiList) {
            EMOJIS.forEach((emoji) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-light m-1';
                button.textContent = emoji;
                button.addEventListener('click', () => {
                    const textarea = el('description');
                    textarea.value += emoji;
                    textarea.dispatchEvent(new Event('input'));
                    textarea.focus();
                    const modalEl = document.getElementById(`${prefix}_emojiModal`);
                    bootstrap.Modal.getInstance(modalEl)?.hide();
                });
                emojiList.appendChild(button);
            });
        }

        // --- Felder (max. 25, Discord-Limit) ---
        function updateFieldCount() {
            const count = fieldsList.querySelectorAll('.eb-field-group').length;
            fieldCountLabel.textContent = `${count} / ${LIMITS.fieldsMax}`;
            fieldCountLabel.classList.toggle('text-danger', count >= LIMITS.fieldsMax);
        }

        function addField() {
            if (fieldsList.querySelectorAll('.eb-field-group').length >= LIMITS.fieldsMax) {
                alert(`Maximal ${LIMITS.fieldsMax} Felder pro Embed erlaubt (Discord-Limit).`);
                return;
            }
            const group = document.createElement('div');
            group.className = 'eb-field-group card card-body mb-2 p-2';

            const nameRow = document.createElement('div');
            nameRow.className = 'd-flex gap-2 mb-1';
            const nameInput = document.createElement('input');
            nameInput.type = 'text';
            nameInput.className = 'form-control form-control-sm eb-field-name';
            nameInput.placeholder = 'Feldname';
            nameInput.maxLength = LIMITS.fieldName;
            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'btn btn-sm btn-outline-danger';
            removeBtn.textContent = '✕';
            removeBtn.title = 'Feld entfernen';
            removeBtn.addEventListener('click', () => {
                group.remove();
                updateFieldCount();
                updatePreview();
            });
            nameRow.appendChild(nameInput);
            nameRow.appendChild(removeBtn);

            const valueInput = document.createElement('textarea');
            valueInput.className = 'form-control form-control-sm eb-field-value mb-1';
            valueInput.rows = 2;
            valueInput.placeholder = 'Feldwert';
            valueInput.maxLength = LIMITS.fieldValue;

            const inlineWrap = document.createElement('div');
            inlineWrap.className = 'form-check';
            const inlineCheckbox = document.createElement('input');
            inlineCheckbox.type = 'checkbox';
            inlineCheckbox.className = 'form-check-input eb-field-inline';
            const inlineLabel = document.createElement('label');
            inlineLabel.className = 'form-check-label small';
            inlineLabel.textContent = 'Inline (nebeneinander anzeigen)';
            inlineWrap.appendChild(inlineCheckbox);
            inlineWrap.appendChild(inlineLabel);

            group.appendChild(nameRow);
            group.appendChild(valueInput);
            group.appendChild(inlineWrap);
            [nameInput, valueInput, inlineCheckbox].forEach((input) => input.addEventListener('input', updatePreview));
            inlineCheckbox.addEventListener('change', updatePreview);

            fieldsList.appendChild(group);
            updateFieldCount();
            updatePreview();
        }
        el('addField')?.addEventListener('click', addField);

        function collectFields() {
            return Array.from(fieldsList.querySelectorAll('.eb-field-group')).map((group) => ({
                name: group.querySelector('.eb-field-name').value.trim(),
                value: group.querySelector('.eb-field-value').value.trim(),
                inline: group.querySelector('.eb-field-inline').checked,
            })).filter((f) => f.name && f.value);
        }

        // --- Gesamtlänge (Discord zählt title+description+alle Feldnamen/
        // -werte+footer.text+author.name zusammen, max. 6000 Zeichen) ---
        function updateTotalCounter(data) {
            if (!totalCounter) return;
            const total = data.title.length + data.description.length + data.footer.length + data.author_name.length
                + data.fields.reduce((sum, f) => sum + f.name.length + f.value.length, 0);
            totalCounter.textContent = `Gesamt: ${total} / ${LIMITS.total} Zeichen`;
            totalCounter.classList.toggle('text-danger', total > LIMITS.total);
        }

        function getEmbedData() {
            return {
                title: el('title')?.value.trim() || '',
                description: el('description')?.value.trim() || '',
                url: el('url')?.value.trim() || '',
                color: (colorHex?.value || colorPicker?.value || '').trim(),
                author_name: el('authorName')?.value.trim() || '',
                author_url: el('authorUrl')?.value.trim() || '',
                author_icon: el('authorIcon')?.value.trim() || '',
                thumbnail: el('thumbnail')?.value.trim() || '',
                banner: el('banner')?.value.trim() || '',
                footer: el('footer')?.value.trim() || '',
                footer_icon: el('footerIcon')?.value.trim() || '',
                timestamp: !!el('timestamp')?.checked,
                fields: collectFields(),
            };
        }

        function isEmpty(data) {
            return !data.title && !data.description && !data.fields.length && !data.thumbnail && !data.banner && !data.author_name;
        }

        // --- Live-Vorschau, orientiert an Discords tatsächlichem Embed-Layout:
        // farbiger Rand links, Autor-Zeile, Titel (verlinkt falls URL gesetzt),
        // Beschreibung, Felder-Grid (inline = nebeneinander), großes Bild unten,
        // Thumbnail oben rechts, Footer-Zeile mit Icon + Zeitstempel ---
        function updatePreview() {
            const data = getEmbedData();
            updateTotalCounter(data);

            if (isEmpty(data)) {
                previewContent.innerHTML = '<p class="text-muted small mb-0">Noch keine Inhalte - fülle das Formular aus, um hier die Vorschau zu sehen.</p>';
                return;
            }

            const color = /^#[0-9a-fA-F]{6}$/.test(data.color) ? data.color : '#2f7a4d';
            const parts = [];

            let authorHtml = '';
            if (data.author_name) {
                authorHtml = `<div class="disc-embed-author">
                    ${data.author_icon ? `<img src="${escapeHtml(data.author_icon)}" class="disc-embed-author-icon" alt="">` : ''}
                    <span>${data.author_url ? `<a href="${escapeHtml(data.author_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(data.author_name)}</a>` : escapeHtml(data.author_name)}</span>
                </div>`;
            }

            let titleHtml = '';
            if (data.title) {
                titleHtml = `<div class="disc-embed-title">${data.url ? `<a href="${escapeHtml(data.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(data.title)}</a>` : escapeHtml(data.title)}</div>`;
            }

            const descriptionHtml = data.description ? `<div class="disc-embed-description">${renderMarkdown(data.description)}</div>` : '';

            let fieldsHtml = '';
            if (data.fields.length) {
                fieldsHtml = `<div class="disc-embed-fields">${data.fields.map((f) => `
                    <div class="disc-embed-field ${f.inline ? 'disc-embed-field-inline' : ''}">
                        <div class="disc-embed-field-name">${escapeHtml(f.name)}</div>
                        <div class="disc-embed-field-value">${renderMarkdown(f.value)}</div>
                    </div>
                `).join('')}</div>`;
            }

            const imageHtml = data.banner ? `<img src="${escapeHtml(data.banner)}" class="disc-embed-image" alt="">` : '';

            let footerHtml = '';
            if (data.footer || data.timestamp) {
                footerHtml = `<div class="disc-embed-footer">
                    ${data.footer_icon ? `<img src="${escapeHtml(data.footer_icon)}" class="disc-embed-footer-icon" alt="">` : ''}
                    <span>${escapeHtml(data.footer)}${data.footer && data.timestamp ? ' • ' : ''}${data.timestamp ? formatTimestamp(new Date()) : ''}</span>
                </div>`;
            }

            const thumbHtml = data.thumbnail ? `<img src="${escapeHtml(data.thumbnail)}" class="disc-embed-thumbnail" alt="">` : '';

            parts.push(`
                <div class="disc-embed" style="border-left-color: ${color};">
                    <div class="disc-embed-main">
                        <div class="disc-embed-body">
                            ${authorHtml}${titleHtml}${descriptionHtml}${fieldsHtml}${imageHtml}${footerHtml}
                        </div>
                        ${thumbHtml}
                    </div>
                </div>
            `);
            previewContent.innerHTML = parts.join('');
        }

        root.querySelectorAll(`#${prefix}_title, #${prefix}_description, #${prefix}_url, #${prefix}_authorName, #${prefix}_authorUrl, #${prefix}_authorIcon, #${prefix}_footer, #${prefix}_footerIcon, #${prefix}_thumbnail, #${prefix}_banner`)
            .forEach((input) => input.addEventListener('input', updatePreview));
        el('timestamp')?.addEventListener('change', updatePreview);

        updatePreview();

        return {
            getEmbedData,
            isEmpty,
            refresh: updatePreview,
        };
    }

    window.initEmbedBuilder = initEmbedBuilder;
})(window, document);
