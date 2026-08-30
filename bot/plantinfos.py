import discord
import random

# Liste der Pflanzen-Embeds
plantinfos = [
#Aloe Vera
    discord.Embed(
        title="Aloe Vera",
        description="Aloe Vera ist eine saftige Pflanze, die für ihre heilenden Eigenschaften bekannt ist.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2020/01/01/10/11/aloe-vera-4733276_640.jpg"),
#Basilikum    
    discord.Embed(
        title="Basilikum",
        description="Basilikum ist ein beliebtes Küchengewürz, das in vielen Gerichten verwendet wird.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2014/10/19/21/49/basil-494751_640.jpg"),
#Tomatenpflanze    
    discord.Embed(
        title="Tomatenpflanze",
        description="Tomatenpflanzen sind ein wichtiger Bestandteil vieler Gärten und können in verschiedenen Variationen gezüchtet werden.",
        color=discord.Color.red(),
		).set_image(url="https://cdn.pixabay.com/photo/2016/08/01/17/08/tomatoes-1561565_640.jpg"),
#Kaktus    
    discord.Embed(
        title="Kaktus",
        description="Kakteen sind perfekt für trockene Umgebungen und benötigen nur wenig Wasser.",
        color=discord.Color.orange(),
		).set_image(url="https://cdn.pixabay.com/photo/2023/02/01/17/58/cactus-7760970_640.jpg"),
#Lavendel    
    discord.Embed(
        title="Lavendel",
        description="Lavendel ist für seinen angenehmen Duft und seine beruhigenden Eigenschaften bekannt.",
        color=discord.Color.purple(),
		).set_image(url="https://cdn.pixabay.com/photo/2016/08/15/14/18/lavender-1595490_640.jpg"),
#Minze    
    discord.Embed(
        title="Minze",
        description="Minze ist ein erfrischendes Kraut, das häufig in Tees und Cocktails verwendet wird.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2018/06/17/21/38/peppermint-3481470_640.jpg"),
#Sonnenblume		
    discord.Embed(
        title="Sonnenblume",
        description="Sonnenblumen sind bekannt für ihre großen gelben Blüten und sind ein Symbol für den Sommer.",
        color=discord.Color.yellow(),
		).set_image(url="https://cdn.pixabay.com/photo/2018/08/18/13/00/sunflower-3614728_640.jpg"),
#Efeu    
    discord.Embed(
        title="Efeu",
        description="Efeu ist eine schnell wachsende Pflanze, die oft als Kletterpflanze verwendet wird.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2017/06/06/03/29/ivy-2376000_640.jpg"),
#Kräuter    
    discord.Embed(
        title="Kräuter",
        description="Kräuter wie Rosmarin, Thymian und Oregano sind vielseitig und können in der Küche verwendet werden.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2024/05/20/14/20/ai-generated-8775370_640.jpg"),
#Gerbera    
    discord.Embed(
        title="Gerbera",
        description="Gerbera ist eine bunte Blume, die in vielen Gärten und als Schnittblume beliebt ist.",
        color=discord.Color.pink(),
		).set_image(url="https://cdn.pixabay.com/photo/2017/08/09/15/43/gerbera-2614932_640.png"),
#Ficus    
    discord.Embed(
        title="Ficus",
        description="Ficus ist eine beliebte Zimmerpflanze, die oft für ihre dekorativen Blätter geschätzt wird.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2018/02/10/19/43/weeping-fig-3144388_640.jpg"),
#Kräutergarten    
    discord.Embed(
        title="Kräutergarten",
        description="Kräutergärten bieten eine Vielzahl an Pflanzen, die in der Küche und für Heilzwecke verwendet werden.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2014/02/16/18/07/cabbage-worm-267422_1280.jpg"),
#Kohlrabi    
    discord.Embed(
        title="Kohlrabi",
        description="Kohlrabi ist ein Gemüse mit mildem Geschmack und einer knackigen Textur.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2013/01/08/01/20/kohlrabi-74276_640.jpg"),
#Zucchini    
    discord.Embed(
        title="Zucchini",
        description="Zucchini ist eine Sommerfrucht, die für ihre Vielseitigkeit in der Küche bekannt ist.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2020/01/24/21/38/vegetables-4791303_640.jpg"),
#Gurkenpflanze    
    discord.Embed(
        title="Gurkenpflanze",
        description="Gurkenpflanzen sind wärmeliebend und brauchen viel Sonne und Wasser.",
        color=discord.Color.green(),
		).set_image(url="https://cdn.pixabay.com/photo/2022/07/15/11/11/cucumbers-7322966_640.jpg"),
#Echinacea    
    discord.Embed(
        title="Echinacea",
        description="Echinacea ist bekannt für ihre immunstärkenden Eigenschaften und wird häufig als Heilpflanze verwendet.",
        color=discord.Color.pink(),
		).set_image(url="https://cdn.pixabay.com/photo/2018/07/05/09/49/purple-coneflower-3517969_640.jpg"),
#Zitronenbaum    
    discord.Embed(
        title="Zitronenbaum",
        description="Zitronenbäume produzieren frische, saure Früchte, die in vielen Rezepten verwendet werden.",
        color=discord.Color.yellow(),
		).set_image(url="https://cdn.pixabay.com/photo/2016/02/18/16/54/citron-1207576_640.jpg"),
#Spathiphyllum    
    discord.Embed(
        title="Spathiphyllum",
        description="Spathiphyllum ist eine pflegeleichte Zimmerpflanze, die auch als Friedenslilie bekannt ist.",
        color=discord.Color(0xFFFFFF),
		).set_image(url="https://cdn.pixabay.com/photo/2020/04/28/05/24/spathiphyllum-5102826_640.jpg"),
#Kallistephus    
    discord.Embed(
        title="Kallistephus",
        description="Kallistephus ist auch bekannt als Sommeraster, eine bunte Blume, die oft in Gärten anzutreffen ist.",
        color=discord.Color.purple(),
		).set_thumbnail(url="https://example.com/kallistephus-thumbnail.jpg"),
#Veilchen    
    discord.Embed(
        title="Veilchen",
        description="Veilchen sind zarte Blumen, die in einer Vielzahl von Farben erhältlich sind.",
        color=discord.Color.purple(),
		).set_thumbnail(url="https://example.com/veilchen-thumbnail.jpg"),
#Hibiskus		
    discord.Embed(
        title="Hibiskus",
        description="Hibiskus ist bekannt für seine großen, auffälligen Blüten und wird oft als Zierpflanze verwendet.",
        color=discord.Color.red(),
		).set_thumbnail(url="https://example.com/hibiskus-thumbnail.jpg"),
#Farn    
    discord.Embed(
        title="Farn",
        description="Farne sind bekannt für ihre zarten, grünen Blätter und ihre Fähigkeit, Schatten zu gedeihen.",
        color=discord.Color.green(),
		).set_thumbnail(url="https://example.com/farn-thumbnail.jpg"),
#Geranium    
    discord.Embed(
        title="Geranium",
        description="Geranien sind robuste Pflanzen, die in Gärten und auf Balkonen beliebt sind.",
        color=discord.Color.pink(),
		).set_thumbnail(url="https://example.com/geranium-thumbnail.jpg"),
#Bromelie    
    discord.Embed(
        title="Bromelie",
        description="Bromelien sind tropische Pflanzen, die für ihre einzigartigen Blüten bekannt sind.",
        color=discord.Color.red(),
		).set_thumbnail(url="https://example.com/bromelie-thumbnail.jpg"),
#Kapuzinerkresse    
    discord.Embed(
        title="Kapuzinerkresse",
        description="Kapuzinerkresse hat leuchtend orangefarbene Blüten und kann als essbare Pflanze in Salaten verwendet werden.",
        color=discord.Color.orange(),
		).set_thumbnail(url="https://example.com/kapuzinerkresse-thumbnail.jpg"),
#Maranta    
    discord.Embed(
        title="Maranta",
        description="Maranta ist eine Zimmerpflanze, die bekannt für ihre dekorativen, gemusterten Blätter ist.",
        color=discord.Color.green(),
		).set_thumbnail(url="https://example.com/maranta-thumbnail.jpg"),
#Lobelie    
    discord.Embed(
        title="Lobelie",
        description="Lobelia ist eine zarte Pflanze, die mit ihren blauen und violetten Blüten in Gärten glänzt.",
        color=discord.Color.blue(),
		).set_thumbnail(url="https://example.com/lobelie-thumbnail.jpg"),
#Cannabis    
    discord.Embed(
    title="Cannabis",
    description=("Cannabis ist eine vielseitige Pflanze, die sowohl für medizinische Zwecke als auch zur Herstellung von Hanfprodukten verwendet wird. "
        "Die Pflanze enthält Cannabinoide wie THC und CBD, die für ihre psychoaktiven und gesundheitlichen Wirkungen bekannt sind. "
        "Cannabis kann indoor und outdoor angebaut werden, wobei Faktoren wie Beleuchtung, Temperatur und Feuchtigkeit eine wichtige Rolle spielen."),
    color=discord.Color.gold(),
		).set_image(url="https://cdn.pixabay.com/photo/2024/11/09/15/16/ai-generated-9186077_640.jpg"),
#Petunie    
    discord.Embed(
        title="Petunie",
        description="Petunien sind farbenfrohe Blumen, die sich gut in hängenden Körben oder auf Beeten machen.",
        color=discord.Color.pink(),
		).set_thumbnail(url="https://example.com/petunie-thumbnail.jpg"),
#Zinnien    
    discord.Embed(
        title="Zinnien",
        description="Zinnien sind leuchtend farbige Blumen, die in Sommergärten nicht fehlen dürfen.",
        color=discord.Color.red(),
    )
    .set_image(url="https://cdn.pixabay.com/photo/2014/05/28/09/46/flower-bed-356359_640.jpg")
    .add_field(
        name="Pflege:",
        value="Zinnien lieben Sonne und durchlässige, nährstoffreiche Erde. Trockenresistent, benötigen jedoch gelegentliches Gießen.",
        inline=True,
    )
    .add_field(
        name="Verwendung:",
        value="Geeignet für Rabatten, Bauerngärten und als haltbare Schnittblumen.",
        inline=True,
    ),
#Ringelblume    
    discord.Embed(
        title="Ringelblume",
        description="Ringelblumen sind nicht nur hübsch, sondern auch für ihre heilenden Eigenschaften bekannt.",
        color=discord.Color.yellow(),
		).set_thumbnail(url="https://example.com/ringelblume-thumbnail.jpg"),
#Rosen    
    discord.Embed(
        title="Rosen",
        description="Rosen sind beliebte Zierpflanzen, die in verschiedenen Farben und Formen erhältlich sind.",
        color=discord.Color.red(),
		).set_thumbnail(url="https://example.com/rosen-thumbnail.jpg"),
#Märzenbecher    
    discord.Embed(
        title="Märzenbecher",
        description="Märzenbecher sind frühe Frühlingsblumen, die oft als erstes bunte Blumen im Garten erscheinen.",
        color=discord.Color(0xFFFFFF),
		).set_thumbnail(url="https://example.com/maerzenbecher-thumbnail.jpg"),
#Tulpen    
    discord.Embed(
        title="Tulpen",
        description="Tulpen sind Frühjahrsblumen, die in vielen Farben und Formen erhältlich sind.",
        color=discord.Color.red(),
		).set_thumbnail(url="https://example.com/tulpen-thumbnail.jpg"),
#Lilie    
    discord.Embed(
        title="Lilie",
        description="Lilien sind eleganten Blumen, die für ihren intensiven Duft und ihre Schönheit geschätzt werden.",
        color=discord.Color(0xFFFFFF),
		).set_thumbnail(url="https://example.com/lilie-thumbnail.jpg"),
#Canna    
    discord.Embed(
        title="Canna",
        description="Canna ist eine tropische Pflanze, die für ihre auffälligen Blüten in Orange und Rot bekannt ist.",
        color=discord.Color.orange(),
		).set_thumbnail(url="https://example.com/canna-thumbnail.jpg"),
#Kornblume    
    discord.Embed(
        title="Kornblume",
        description="Kornblumen sind klassische Wildblumen, die in Gärten und auf Feldern wachsen.",
        color=discord.Color.blue(),
		).set_thumbnail(url="https://example.com/kornblume-thumbnail.jpg"),
#Monstera Thai Constellation                
    discord.Embed(
        title="Monstera Thai Constellation",
        description="Diese auffällige Monstera-Sorte besticht durch ihre cremefarbenen Panaschierungen.",
        color=discord.Color.green(),
        #).set_image(url="")
        )
        .add_field(name="Pflege:", 
        value="Liebt helle, indirekte Sonne. Geringer Wasserbedarf, Erde leicht feucht halten.", 
        inline=True
        ,)
        .add_field(name="Verwendung:", 
        value="Ideal als Zimmerpflanze für dekorative Akzente in Wohnräumen.", 
        inline=True,
        ),
#Alocasia Frydek Variegated        
    discord.Embed(
        title="Alocasia Frydek Variegated",
        description="Eine seltene Alocasia mit samtig dunkelgrünen Blättern und weißer Panaschierung.",
        color=discord.Color.teal(),
        #).set_image(url="")
        )
        .add_field(
        name="Pflege:",
        value="Hohe Luftfeuchtigkeit, indirektes Licht, mäßiges Gießen.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Tropisches Flair als Blickfang im Wohnzimmer oder Gewächshaus.",
        inline=True,
        ),
#Avocado        
     discord.Embed(
        title="Avocado",
        description="Ein tropischer Baum, dessen Früchte weltweit beliebt sind.",
        color=discord.Color.green(),
        )
        #.set_image(url="")
        .add_field(
        name="Pflege:",
        value="Liebt viel Licht und regelmäßiges Gießen, Staunässe vermeiden.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Als Zimmerpflanze gezogen; liefert bei idealen Bedingungen Avocados.",
        inline=True,
        ),
#Dahlie        
     discord.Embed(
        title="Dahlie",
        description="Dahlien bezaubern mit ihren farbenfrohen Blüten und Formenvielfalt.",
        color=discord.Color.orange(),
        )
        .set_image(url="https://cdn.pixabay.com/photo/2016/07/17/18/35/dahlia-1524455_640.jpg")
        .add_field(
        name="Pflege:",
        value="Benötigt Sonne und durchlässigen Boden. Regelmäßige Bewässerung nötig.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Wunderbar für Beete, Rabatten und als Schnittblume.",
        inline=True,
        ),
#Alocasia Pink Dragon        
     discord.Embed(
        title="Alocasia Pink Dragon",
        description="Elegante Alocasia mit dunkelgrünen Blättern und pinkfarbenen Stielen.",
        color=discord.Color.pink(),
        )
        #.set_image(url="")
        .add_field(
        name="Pflege:",
        value="Liebt hohe Luftfeuchtigkeit und indirektes Licht. Erde feucht, nicht nass.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Perfekt als exotischer Akzent in der Wohnung.",
        inline=True,
        ),
#Dieffenbachie 'Tropic Snow'       
     discord.Embed(
        title="Dieffenbachie 'Tropic Snow'",
        description="Eine attraktive Zimmerpflanze mit großen, grün-weiß gemusterten Blättern.",
        color=discord.Color.green(),
        )
        #.set_image(url="")
        .add_field(
        name="Pflege:",
        value="Halbschatten bevorzugt, Erde gleichmäßig feucht halten.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Beliebt für Büroräume und Wohnbereiche als Luftverbesserer.",
        inline=True,
        ),
#Korkenzieherbinse        
     discord.Embed(
        title="Korkenzieherbinse",
        description="Ein Gras mit spiralförmig gedrehten Stängeln, das Struktur verleiht.",
        color=discord.Color.dark_green(),
        )
        #.set_image(url="")
        .add_field(
        name="Pflege:",
        value="Liebt feuchte Standorte und volle Sonne bis Halbschatten.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Ideal für Teichränder, Feuchtbereiche oder als Zimmerpflanze.",
        inline=True,
        ),
#Monstera Obliqua 'Peru'        
     discord.Embed(
        title="Monstera Obliqua 'Peru'",
        description="Eine sehr seltene Monstera-Art mit feinen Blattlöchern.",
        color=discord.Color.dark_green(),
        )
        #.set_image(url="")
        .add_field(
        name="Pflege:",
        value="Hohe Luftfeuchtigkeit, indirektes Licht, leicht feuchte Erde.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Ein Juwel für Sammler seltener Pflanzen.",
        inline=True
        ),
#Alocasia Zebrina
     discord.Embed(
        title="Alocasia Zebrina",
        description="Alocasia Zebrina zeichnet sich durch ihre markanten, zebrastreifenartigen Stiele und tropischen Look aus.",
        color=discord.Color.green(),
        )
        .add_field(
        name="Pflege:",
        value="Benötigt hohe Luftfeuchtigkeit und indirektes Licht. Die Erde sollte immer leicht feucht gehalten werden.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Ideal als tropische Zimmerpflanze für moderne Innenräume.",
        inline=True
        ),
#Epipremnum pinnatum - Efeutute
    discord.Embed(
        title="Epipremnum pinnatum - Efeutute",
        description="Efeututen sind pflegeleichte Kletterpflanzen, die durch ihre glänzenden, herzförmigen Blätter bestechen.",
        color=discord.Color.green(),
        )
        .add_field(
        name="Pflege:",
        value="Liebt helles, indirektes Licht und benötigt nur wenig Wasser. Ideal für Anfänger.",
        inline=True,
        )
        .add_field(
        name="Verwendung:",
        value="Hängend oder kletternd als dekorative Pflanze für Wohnzimmer und Büros.",
        inline=True
        ),
#Syngonium 'Milk confetti'
     discord.Embed(
        title="Syngonium 'Milk confetti'",
        description="Syngonium 'Milk confetti' ist eine wunderschöne Kletterpflanze mit einzigartigem, cremefarbenen Blattmuster.",
        color=discord.Color.green()
        ).add_field(
        name="Pflege:",
        value="Bevorzugt indirektes Licht und eine hohe Luftfeuchtigkeit. Die Erde sollte gleichmäßig feucht gehalten werden.",
        inline=True
        ).add_field(
        name="Verwendung:",
        value="Ideal als hängende Pflanze oder für die Kletterhilfe in Innenräumen.",
        inline=True
        ),
#Dracaena
     discord.Embed(
        title="Dracaena",
        description="Die Dracaena ist eine robuste, tropische Pflanze, die sich gut für Innenräume eignet.",
        color=discord.Color.green()
        ).add_field(
        name="Pflege:",
        value="Liebt indirektes Licht und mäßige Bewässerung. Staunässe sollte vermieden werden.",
        inline=True
        ).add_field(
        name="Verwendung:",
        value="Ideal für Wohnzimmer und Büros, um tropisches Flair zu verleihen.",
        inline=True
        ),
#Philodendron tortum
     discord.Embed(
        title="Philodendron tortum",
        description="Der Philodendron tortum hat außergewöhnlich gezackte Blätter und ist eine interessante Zierpflanze.",
        color=discord.Color.green()
        ).add_field(
        name="Pflege:",
        value="Liebt feuchte Erde und indirektes Licht. Regelmäßig gießen, aber Staunässe vermeiden.",
        inline=True
        ).add_field(
        name="Verwendung:",
        value="Perfekt als dekorative Pflanze in modernen Wohnungen.",
        inline=True
        ),
#Raphidophora Tetrasperma
     discord.Embed(
        title="Raphidophora Tetrasperma",
        description="Raphidophora Tetrasperma ist eine kletternde Pflanze mit löchrigen, ovalen Blättern.",
        color=discord.Color.green()
        ).add_field(
        name="Pflege:",
        value="Mäßiges Gießen und indirektes Licht sind ideal. Diese Pflanze ist einfach zu pflegen.",
        inline=True
        ).add_field(
        name="Verwendung:",
        value="Hängend oder kletternd in Innenräumen, besonders geeignet für Regale oder als Kletterpflanze.",
        inline=True
        ),
#Philodendron Billiatae
     discord.Embed(
        title="Philodendron Billiatae",
        description="Philodendron Billiatae zeichnet sich durch seine attraktiven, dunklen Blätter mit helleren Adern aus.",
        color=discord.Color.green()
        ).add_field(
        name="Pflege:",
        value="Mäßig feuchte Erde und indirektes Licht. Die Pflanze ist pflegeleicht und benötigt wenig Wasser.",
        inline=True
        ).add_field(
        name="Verwendung:",
        value="Ideal für tropische Innenräume oder als dekorative Pflanze für den Schreibtisch.",
        inline=True
        )
]
