/**
 * Wiederverwendbare Discord-Mitglieder-Suche.
 *
 * Lädt einmal /api/members (aktuelle Server-Mitgliederliste), und filtert
 * client-seitig nach Name/Anzeigename/ID während der Nutzer tippt. Bei Auswahl
 * eines Vorschlags wird die Discord-User-ID in das Eingabefeld übernommen.
 *
 * Wichtig: Das Eingabefeld bleibt ein ganz normales Textfeld - manuelle
 * ID-Eingabe (z.B. für Nutzer, die den Server bereits verlassen haben und
 * daher nicht in der Mitgliederliste auftauchen) funktioniert weiterhin genauso,
 * die Vorschlagsliste ist nur eine zusätzliche Hilfe, kein Zwang.
 *
 * Nutzung: attachMemberPicker('inputId', 'previewId')
 * - inputId: die ID des Textfelds, das am Ende die User-ID enthalten soll
 * - previewId: (optional) die ID eines Elements, in dem "Ausgewählt: Name" angezeigt wird
 */
async function attachMemberPicker(inputId, previewId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const preview = previewId ? document.getElementById(previewId) : null;

    let members = [];
    try {
        const res = await fetch('/api/members');
        if (res.ok) {
            const data = await res.json();
            members = data.members || [];
        }
    } catch (e) {
        console.error('Mitgliederliste konnte nicht geladen werden:', e);
    }

    const list = document.createElement('div');
    list.className = 'list-group position-absolute w-100 shadow-sm';
    list.style.zIndex = '1000';
    list.style.maxHeight = '220px';
    list.style.overflowY = 'auto';
    list.style.display = 'none';
    if (getComputedStyle(input.parentElement).position === 'static') {
        input.parentElement.style.position = 'relative';
    }
    input.parentElement.appendChild(list);

    function showMatches(query) {
        list.innerHTML = '';
        if (!query || members.length === 0) {
            list.style.display = 'none';
            return;
        }
        const q = query.toLowerCase();
        const matches = members.filter((m) =>
            m.username.toLowerCase().includes(q) ||
            m.display_name.toLowerCase().includes(q) ||
            m.id.includes(q)
        ).slice(0, 8);

        if (!matches.length) {
            list.style.display = 'none';
            return;
        }
        matches.forEach((m) => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'list-group-item list-group-item-action py-1';
            item.textContent = `${m.display_name} (${m.username}) — ${m.id}`;
            item.addEventListener('mousedown', (e) => {
                // mousedown statt click, damit es vor dem "blur" des Inputs feuert
                e.preventDefault();
                input.value = m.id;
                if (preview) {
                    preview.textContent = `Ausgewählt: ${m.display_name} (${m.username})`;
                    preview.className = 'form-text text-success';
                }
                list.style.display = 'none';
            });
            list.appendChild(item);
        });
        list.style.display = 'block';
    }

    input.addEventListener('input', () => showMatches(input.value.trim()));
    input.addEventListener('focus', () => showMatches(input.value.trim()));
    input.addEventListener('blur', () => {
        setTimeout(() => { list.style.display = 'none'; }, 100);
    });
}
