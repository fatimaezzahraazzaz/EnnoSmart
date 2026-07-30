from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_14_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_14_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit générique / administratif
    "nous",
    "notre",
    "nos",
    "utilisateur",
    "l'utilisateur",
    "acteurs du secteur",
    "laboratoires d’essais",
    "laboratoires d'essais",
    "équipementiers et spécialistes des systèmes d’armes de pointe actuels",
    "équipementiers et spécialistes des systèmes d'armes de pointe actuels",
    "crédit impôt recherche",
    "coût total",
    "000 000 €",
    "000\u00a0000 €",
    "20xx",
    "thésaurus",
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",

    # Faux lieux / contexte
    "environnement de défense",
    "etats-unis",
    "états-unis",
    "pc",

    # Dates / valeurs techniques mal détectées comme DATE_PERIODE
    "date",
    "date précise",
    "front montant",
    "55h",
    "aah",
    "ffff_ffffh",
    "début des années 2000",

    # Montants faux / unités techniques
    "512 octets",
    "128 octets",
    "32 octets",
    "530 octets",
    "1 octet",
    "2 octets",
    "4 octets",
    "6 octets",
    "8-bits",
    "16-bits",
    "32-bits",

    # Trop générique
    "objectifs",
    "objectif",
    "résultats",
    "résultat",
    "banc",
    "système",
    "solution technique",
}

AUTO_FIX = {
    # ORGANISMES
    "safran-ed": "ORGANISME",
    "safran ed": "ORGANISME",
    "safran-ed bmo laser": "ORGANISME",
    "ametra": "ORGANISME",
    "theoris": "ORGANISME",
    "théoris": "ORGANISME",
    "dga": "ORGANISME",
    "lockheed martin corporation": "ORGANISME",
    "geotest marvin inc": "ORGANISME",
    "gsel": "ORGANISME",
    "guidance system evaluation laboratory": "ORGANISME",

    # DOMAINES
    "secteur de défense": "DOMAINE_RD",
    "domaine militaire": "DOMAINE_RD",
    "électronique embarquée": "DOMAINE_RD",
    "electronique embarquée": "DOMAINE_RD",
    "optronique": "DOMAINE_RD",
    "optique": "DOMAINE_RD",
    "électronique": "DOMAINE_RD",
    "systèmes de télécommunications et réseaux": "DOMAINE_RD",
    "systèmes informatiques": "DOMAINE_RD",
    "systèmes de guidage": "DOMAINE_RD",
    "guidage laser": "DOMAINE_RD",
    "guidage infrarouge": "DOMAINE_RD",
    "guidage électro-optique": "DOMAINE_RD",
    "guidage electro-optique": "DOMAINE_RD",
    "armes guidées": "DOMAINE_RD",
    "systèmes d’armes": "DOMAINE_RD",
    "systèmes d'armes": "DOMAINE_RD",

    # EQUIPEMENTS / SYSTEMES
    "bmo": "EQUIPEMENT_RD",
    "bmo laser": "EQUIPEMENT_RD",
    "bmo ir": "EQUIPEMENT_RD",
    "bmo ng": "EQUIPEMENT_RD",
    "pc-bmo": "EQUIPEMENT_RD",
    "pc bmo": "EQUIPEMENT_RD",
    "banc de mise en œuvre": "EQUIPEMENT_RD",
    "banc de mise en oeuvre": "EQUIPEMENT_RD",
    "banc de test": "EQUIPEMENT_RD",
    "bancs de test": "EQUIPEMENT_RD",
    "banc d’essai": "EQUIPEMENT_RD",
    "banc d'essai": "EQUIPEMENT_RD",
    "banc d’essais": "EQUIPEMENT_RD",
    "banc d'essais": "EQUIPEMENT_RD",
    "système de test": "EQUIPEMENT_RD",
    "systèmes de test": "EQUIPEMENT_RD",
    "station de test": "EQUIPEMENT_RD",
    "missile": "EQUIPEMENT_RD",
    "missiles": "EQUIPEMENT_RD",
    "missile tow": "EQUIPEMENT_RD",
    "missiles tow": "EQUIPEMENT_RD",
    "mica ng": "EQUIPEMENT_RD",
    "armes à guidage laser": "EQUIPEMENT_RD",
    "arme guidée": "EQUIPEMENT_RD",
    "armes guidées": "EQUIPEMENT_RD",
    "autodirecteur": "EQUIPEMENT_RD",
    "autodirecteur laser": "EQUIPEMENT_RD",
    "autodirecteur à guidage laser": "EQUIPEMENT_RD",
    "autodirecteur à guidage infrarouge": "EQUIPEMENT_RD",
    "ad laser": "EQUIPEMENT_RD",
    "ad ir": "EQUIPEMENT_RD",
    "lst": "EQUIPEMENT_RD",
    "spu": "COMPOSANT_TECHNIQUE",
    "uut": "EQUIPEMENT_RD",
    "pc-exe": "EQUIPEMENT_RD",
    "pc-ihm": "EQUIPEMENT_RD",
    "pc-exécution": "EQUIPEMENT_RD",
    "pc-execution": "EQUIPEMENT_RD",
    "unité de contrôle banc": "EQUIPEMENT_RD",

    # TECHNOLOGIES / OUTILS / PROTOCOLES
    "ethernet": "TECHNOLOGIE_RD",
    "gb ethernet": "TECHNOLOGIE_RD",
    "gbit ethernet": "TECHNOLOGIE_RD",
    "ethernet gigabit": "TECHNOLOGIE_RD",
    "gigabit ethernet": "TECHNOLOGIE_RD",
    "udp": "TECHNOLOGIE_RD",
    "udp/ip": "TECHNOLOGIE_RD",
    "ipv4": "TECHNOLOGIE_RD",
    "pcap": "TECHNOLOGIE_RD",
    "rs485": "TECHNOLOGIE_RD",
    "rs422": "TECHNOLOGIE_RD",
    "rs232": "TECHNOLOGIE_RD",
    "lvds": "TECHNOLOGIE_RD",
    "lvttl": "TECHNOLOGIE_RD",
    "ttl": "TECHNOLOGIE_RD",
    "uart": "TECHNOLOGIE_RD",
    "uarts": "TECHNOLOGIE_RD",
    "vxworks": "TECHNOLOGIE_RD",
    "rtx64": "TECHNOLOGIE_RD",
    "windows 10 iot": "TECHNOLOGIE_RD",
    "windows 10": "TECHNOLOGIE_RD",
    "windows10": "TECHNOLOGIE_RD",
    "os temps réel": "TECHNOLOGIE_RD",
    "os temps reel": "TECHNOLOGIE_RD",
    "xlinx": "TECHNOLOGIE_RD",
    "xilinx": "TECHNOLOGIE_RD",
    "cots": "TECHNOLOGIE_RD",
    "afdx": "TECHNOLOGIE_RD",
    "avionics full duplex switched ethernet": "TECHNOLOGIE_RD",
    "pxi": "TECHNOLOGIE_RD",
    "gpib": "TECHNOLOGIE_RD",

    # COMPOSANTS TECHNIQUES
    "fpga": "COMPOSANT_TECHNIQUE",
    "module fpga kintex 7325t": "COMPOSANT_TECHNIQUE",
    "fpga kintex-7": "COMPOSANT_TECHNIQUE",
    "module fpga kintex-7": "COMPOSANT_TECHNIQUE",
    "fpga pfp": "COMPOSANT_TECHNIQUE",
    "fmc": "COMPOSANT_TECHNIQUE",
    "carte fmc": "COMPOSANT_TECHNIQUE",
    "fpga mezzanine card": "COMPOSANT_TECHNIQUE",
    "bie": "COMPOSANT_TECHNIQUE",
    "boitier d’interface électronique": "COMPOSANT_TECHNIQUE",
    "boitier d'interface electronique": "COMPOSANT_TECHNIQUE",
    "boitier d’interface électrique": "COMPOSANT_TECHNIQUE",
    "boitier d'interface électrique": "COMPOSANT_TECHNIQUE",
    "carte transceiver/sbs/claquage": "COMPOSANT_TECHNIQUE",
    "transceiver/sbs/claquage": "COMPOSANT_TECHNIQUE",
    "transceiver rs485": "COMPOSANT_TECHNIQUE",
    "claquage bouteille": "COMPOSANT_TECHNIQUE",
    "module de diode laser": "COMPOSANT_TECHNIQUE",
    "module de diode laser à semi-conducteur": "COMPOSANT_TECHNIQUE",
    "circuit intégré": "COMPOSANT_TECHNIQUE",
    "microcontrôleur": "COMPOSANT_TECHNIQUE",
    "module convertisseur dc-dc": "COMPOSANT_TECHNIQUE",
    "composants électro-optiques": "COMPOSANT_TECHNIQUE",
    "lasers": "COMPOSANT_TECHNIQUE",
    "systèmes de lumière": "COMPOSANT_TECHNIQUE",
    "miroir à cardan motorisé": "COMPOSANT_TECHNIQUE",
    "composants mécaniques et optiques": "COMPOSANT_TECHNIQUE",
    "capteur laser": "COMPOSANT_TECHNIQUE",
    "ordinateur de guidage": "COMPOSANT_TECHNIQUE",
    "liaisons série": "COMPOSANT_TECHNIQUE",
    "liaison série": "COMPOSANT_TECHNIQUE",
    "architecture logicielle": "COMPOSANT_TECHNIQUE",
    "architecture matérielle": "COMPOSANT_TECHNIQUE",
    "architecture fpga": "COMPOSANT_TECHNIQUE",
    "architecture de traitement": "COMPOSANT_TECHNIQUE",
    "mécanisme de synchronisation des traitements de données": "COMPOSANT_TECHNIQUE",
    "mécanisme de pilotage des alimentations": "COMPOSANT_TECHNIQUE",
    "bloc io_gen": "COMPOSANT_TECHNIQUE",
    "io_gen": "COMPOSANT_TECHNIQUE",
    "bloc mac tx": "COMPOSANT_TECHNIQUE",
    "bloc mac_tx": "COMPOSANT_TECHNIQUE",
    "mac tx": "COMPOSANT_TECHNIQUE",
    "mac_tx": "COMPOSANT_TECHNIQUE",
    "mac rx": "COMPOSANT_TECHNIQUE",
    "rx_process": "COMPOSANT_TECHNIQUE",
    "fifo": "COMPOSANT_TECHNIQUE",
    "fifos": "COMPOSANT_TECHNIQUE",
    "fifo_in": "COMPOSANT_TECHNIQUE",
    "fifo_frame": "COMPOSANT_TECHNIQUE",
    "fifo_tx": "COMPOSANT_TECHNIQUE",
    "fifo_rx": "COMPOSANT_TECHNIQUE",
    "dpram": "COMPOSANT_TECHNIQUE",
    "pll": "COMPOSANT_TECHNIQUE",
    "horloges": "COMPOSANT_TECHNIQUE",
    "blocs de traitement": "COMPOSANT_TECHNIQUE",
    "transceivers haut débit": "COMPOSANT_TECHNIQUE",
    "plugin": "COMPOSANT_TECHNIQUE",
    "fonct": "COMPOSANT_TECHNIQUE",
    "instrum": "COMPOSANT_TECHNIQUE",
    "shm": "COMPOSANT_TECHNIQUE",
    "mémoire partagée": "COMPOSANT_TECHNIQUE",
    "message queue": "COMPOSANT_TECHNIQUE",
    "queues de messages": "COMPOSANT_TECHNIQUE",

    # Liaisons / blocs spécifiques
    "rs_telem": "COMPOSANT_TECHNIQUE",
    "rs telem": "COMPOSANT_TECHNIQUE",
    "liaison rs telem": "COMPOSANT_TECHNIQUE",
    "rs_test": "COMPOSANT_TECHNIQUE",
    "rs_test_rx": "COMPOSANT_TECHNIQUE",
    "rs_pld_tx": "COMPOSANT_TECHNIQUE",
    "rs_pld_rx": "COMPOSANT_TECHNIQUE",
    "rs_dial_tx": "COMPOSANT_TECHNIQUE",
    "rs_dial_rx": "COMPOSANT_TECHNIQUE",
    "rs_mission_tx": "COMPOSANT_TECHNIQUE",
    "rs_mission_rx": "COMPOSANT_TECHNIQUE",
    "rs_plot": "COMPOSANT_TECHNIQUE",
    "stm_wr": "COMPOSANT_TECHNIQUE",
    "stm_rd": "COMPOSANT_TECHNIQUE",
    "stm_tx": "COMPOSANT_TECHNIQUE",
    "stm_rx": "COMPOSANT_TECHNIQUE",
    "stm_ck_middle": "COMPOSANT_TECHNIQUE",
    "err_stop": "COMPOSANT_TECHNIQUE",
    "err_gap": "COMPOSANT_TECHNIQUE",
    "err_reponse": "COMPOSANT_TECHNIQUE",
    "err_qty": "COMPOSANT_TECHNIQUE",
    "safran-ed bmo": "ORGANISME",
    "microsystème": "DOMAINE_RD",
    "microsystèmes": "DOMAINE_RD",
    "nanosystèmes": "DOMAINE_RD",
    "mac_rx": "COMPOSANT_TECHNIQUE",
    "mac rx": "COMPOSANT_TECHNIQUE",
    "spu_eca_dial_j1": "COMPOSANT_TECHNIQUE",
    "spu_lst_dial": "COMPOSANT_TECHNIQUE",
    "eca_spu_dial_j1": "COMPOSANT_TECHNIQUE",
    "lst_spu_dial": "COMPOSANT_TECHNIQUE",
    # METHODES
    "démarche expérimentale": "METHODE_RD",
    "etat de l’art externe": "METHODE_RD",
    "état de l’art externe": "METHODE_RD",
    "analyse de l’état de l’art": "METHODE_RD",
    "analyse de l'état de l'art": "METHODE_RD",
    "simulation informatique en temps réel": "METHODE_RD",
    "simulation/hardware": "METHODE_RD",
    "tests électro-optiques": "METHODE_RD",
    "essais": "METHODE_RD",
    "étude des performances": "METHODE_RD",
    "développement logiciel": "METHODE_RD",
    "traitement des trames": "METHODE_RD",
    "conversion des liaisons série": "METHODE_RD",
    "datation des messageries": "METHODE_RD",
    "dimensionnement des fifo": "METHODE_RD",
    "gestion des débordements": "METHODE_RD",
    "contrôle de flux": "METHODE_RD",
    "correction des trames": "METHODE_RD",

    # VERROUS
    "verrou": "VERROU_TECH",
    "verrous technologiques": "VERROU_TECH",
    "difficulté technique": "VERROU_TECH",
    "complexité du développement des cartes électroniques": "VERROU_TECH",
    "décodage des trames": "VERROU_TECH",
    "perte de données": "VERROU_TECH",
    "congestion": "VERROU_TECH",
    "saturation": "VERROU_TECH",
    "réactivité": "VERROU_TECH",
    "temps de réponse très court": "VERROU_TECH",
    "synchronisation des flux": "VERROU_TECH",
    "jitter": "VERROU_TECH",

    # OBJECTIFS / RESULTATS
    "objectifs de rapidité de calcul et de réactivité": "OBJECTIF_RD",
    "forte précision et réactivité": "OBJECTIF_RD",
    "réaction de l’ordre du μs": "OBJECTIF_RD",
    "synchronisation des flux à 2µs avec un jitter de 1µs": "OBJECTIF_RD",
}

SECTION_TITLES = {
    "intitulé du projet": "OBJECTIF_RD",
    "objectifs visés et performances à atteindre": "OBJECTIF_RD",
    "contexte du projet": "DOMAINE_RD",
    "etat de l’art externe": "METHODE_RD",
    "état de l’art externe": "METHODE_RD",
    "insuffisances des solutions existantes": "VERROU_TECH",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "démarche expérimentale, travaux r&d réalisés": "METHODE_RD",
    "partenariats & phasage des travaux": "METHODE_RD",
    "recherche et définition de la nouvelle architecture du banc de mise en œuvre de l’autodirecteur à guidage infrarouge": "METHODE_RD",
    "définition et analyse de l’architecture de la carte transceiver/sbs/claquage": "METHODE_RD",
    "définition de l’architecture logicielle du bmo ir": "METHODE_RD",
    "définition de l’architecture matérielle du banc": "METHODE_RD",
    "définition de l’architecture logicielle du banc de mise en œuvre de l’autodirecteur à guidage laser": "METHODE_RD",
    "recherche et développement d’une solution technique permettant la conversion des liaisons série de l’autodirecteur laser en liaisons gbit ethernet": "METHODE_RD",
    "architecture de traitement de la liaison série rs telem": "METHODE_RD",
    "définition de l’architecture de traitement de la liaison rs test": "METHODE_RD",
    "définition de l’architecture de traitement de la liaison rs_test_rx": "METHODE_RD",
    "définition de l’architecture de la liaison rs_pld_tx": "METHODE_RD",
    "définition de l’architecture de traitement de la liaison rs_pld_rx": "METHODE_RD",
    "définition de l’architecture de traitement de la liaison rs dial tx": "METHODE_RD",
    "définition de l’architecture de traitement de rs_dial_rx": "METHODE_RD",
    "définition de l’architecture du bloc io_gen": "METHODE_RD",
    "définition de l’architecture du bloc mac tx": "METHODE_RD",
    "conclusion et contribution scientifique, technique ou technologique": "RESULTAT_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^année\s+\d{4}$",
    r"^ann[ée]e\s+\d{4}$",
    r"^\d{4}\s*-\s*\d{4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def is_placeholder_money(text: str) -> bool:
    t = lower_norm(text).replace("\u00a0", " ")
    return t in {"000 000 €", "coût total"} or bool(re.fullmatch(r"0+\s*0*\s*0*\s*€.*", t))

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # Titres de section utiles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # Suppression directe
    if low in REMOVE_EXACT:
        return None

    # URLs / emails / admin
    if "@" in text or low.startswith("http") or low.startswith("www.") or low.endswith(".com") or low.endswith(".fr"):
        return None

    # Montants placeholders ou unités techniques
    if label == "MONTANT_CIR":
        if is_placeholder_money(text):
            return None
        if re.search(r"\b(octets?|bits?|mhz|gb/s|mbit|ns|µs|ms|m)\b", low):
            return None
        if low in {"front montant", "bonus", "malus"}:
            return None

    # Dates : garder seulement vraies années/périodes
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None
        ent["text"] = text
        return ent

    # Faux personnes
    if label == "PERSONNE":
        if low in {"utilisateur", "l'utilisateur", "chef de projet"}:
            return None

    # Faux organismes
    if label == "ORGANISME":
        if low in {
            "nous", "acteurs du secteur", "laboratoires d’essais",
            "laboratoires d'essais", "crédit impôt recherche"
        }:
            return None

    # Faux lieux
    if label == "LIEU":
        if low in {"environnement de défense", "pc", "etats-unis", "états-unis"}:
            return None

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns BMO / banc
    if re.search(r"\b(bmo|pc-bmo|pc bmo|bmo laser|bmo ir|bmo ng)\b", low):
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "banc de mise" in low or "banc de test" in low or "banc d’essai" in low or "banc d'essai" in low:
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    # Patterns protocols / communication
    if re.search(r"\b(ethernet|udp|ipv4|pcap|rs485|rs422|rs232|lvds|lvttl|uart|pxi|gpib|afdx)\b", low):
        if label in {"ETP", "LIEU", "ORGANISME", "DATE_PERIODE"}:
            ent["label"] = "TECHNOLOGIE_RD"
            ent["status"] = "force_cleaned"

    # Patterns composants
    if re.search(r"\b(fpga|fifo|dpram|pll|fmc|bie|io_gen|mac_tx|mac tx|rx_process|stm_|spu|lst)\b", low):
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if re.search(r"\b(rs_telem|rs_test|rs_test_rx|rs_pld_tx|rs_pld_rx|rs_dial_tx|rs_dial_rx|rs_mission_tx|rs_mission_rx)\b", low):
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "autodirecteur" in low or low in {"ad ir", "ad laser"}:
        ent["label"] = "EQUIPEMENT_RD"
        ent["status"] = "force_cleaned"

    if "liaison série" in low or "liaisons série" in low:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    if "verrou" in low or "difficulté technique" in low or "perte de données" in low or "saturation" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if "architecture" in low and label in {"ORGANISME", "TECHNOLOGIE_RD", "EQUIPEMENT_RD"}:
        ent["label"] = "COMPOSANT_TECHNIQUE"
        ent["status"] = "force_cleaned"

    # Nettoyage générique par label
    if label == "OBJECTIF_RD" and low in {"objectif", "objectifs"}:
        return None

    if label == "RESULTAT_RD" and low in {"résultat", "résultats", "résultats obtenus"}:
        return None

    if len(text) <= 2:
        return None

    ent["text"] = text
    return ent

def deduplicate(entities):
    seen = set()
    out = []

    for ent in entities:
        key = (
            lower_norm(ent.get("text", "")),
            ent.get("label"),
            ent.get("start"),
            ent.get("end"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ent)

    return out

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")
        old_text = ent.get("text")

        cleaned = clean_entity(dict(ent))

        if cleaned is None:
            removed += 1
            continue

        if (
            cleaned.get("label") != old_label
            or cleaned.get("text") != old_text
            or cleaned.get("status") == "force_cleaned"
        ):
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = deduplicate(new_entities)
    item["annotation_status"] = "force_cleaned"
    after += len(item["entities"])

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean projet 14 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")