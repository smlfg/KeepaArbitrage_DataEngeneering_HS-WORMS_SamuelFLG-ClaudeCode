# Claude Code Chrome Extension — Der Browser-Brückenbauer

## 1. Was ist das?

Stell dir vor, du sitzt im Terminal und plötzlich kannst du mit deinem Browser sprechen. Nicht irgendwie über Umwege, sondern direkt. Claude Code öffnet Tabs, klickt auf Buttons, liest den Seiteninhalt und macht Screenshots — als wäre der Browser ein williges Werkzeug in deinen Händen.

Das ist keine Magie. Das ist **NativeMessaging** — eine API von Chrome, die es externen Programmen erlaubt, mit dem Browser zu kommunizieren. Claude Code nutzt das, um eine Brücke zwischen zwei Welten zu bauen: dem Terminal und dem Chrome-Browser.

Warum ist das nützlich? Weil der Browser eine Menge Daten hat, die über APIs nicht zugänglich sind. Bestseller-Listen, dynamisch geladene Inhalte, JavaScript-gerenderte Seiten — all das siehst du im Browser, aber APIs geben es nicht her. Mit der Chrome-Extension sprichst du direkt mit dem DOM und holst dir genau die Daten, die du brauchst.

---

## 2. Architektur-Diagramm

So fließen die Daten vom Terminal bis zum Browser-Tab:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TERMINAL (Claude Code)                           │
│                              claude --chrome                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ stdio (JSON über stdin/stdout)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        NATIVE MESSAGING HOST                                │
│                   ~/.claude/chrome/chrome-native-host                      │
│                        (Shell-Wrapper Script)                              │
│                         ⬆                                                    │
│                   Ruft Claude Code Binary mit                              │
│                   --chrome-native-host Flag auf                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ Chrome Native Messaging Protocol
                                       │ (JSON-Nachrichten mit 4-Byte Header)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CHROME EXTENSION                                         │
│              chrome-extension://fcoeoabgfenejglbffodgkkbkcdhcgfn/          │
│                                                                             │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                   │
│   │   getTabs    │   │ navigateTo   │   │   click      │   ...             │
│   └──────────────┘   └──────────────┘   └──────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CHROME BROWSER                                    │
│                                                                             │
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐                      │
│   │ Tab #1  │  │ Tab #2  │  │ Tab #3  │  │ Tab #4  │  ...                 │
│   │ Amazon  │  │ Keepa   │  │ YouTube │  │ Docs    │                      │
│   └─────────┘  └─────────┘  └─────────┘  └─────────┘                      │
│                                                                             │
│                              🌐 Webseiten + DOM                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Der Datenweg in Kurzform:**

1. Du tippst im Terminal einen Chrome-Befehl
2. Claude Code sendet eine JSON-Nachricht an den NativeMessaging Host
3. Der Host leitet sie via stdio an die Chrome Extension weiter
4. Die Extension führt die Aktion im Browser aus
5. Das Ergebnis geht den gleichen Weg zurück

---

## 3. Wie funktioniert die Verbindung?

Vier Komponenten müssen zusammenspielen — jede hat eine klar definierte Rolle.

### 3.1 NativeMessaging Host JSON

Das ist die **Visitenkarte**. Chrome weiß durch diese Datei, wo der externe Host zu finden ist und welchen Extensions er vertraut.

**Pfad:** `~/.config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json`

```json
{
  "name": "com.anthropic.claude_code_browser_extension",
  "description": "Claude Code Browser Extension Native Host",
  "path": "/home/smlflg/.claude/chrome/chrome-native-host",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://fcoeoabgfenejglbffodgkkbkcdhcgfn/"
  ]
}
```

**Wichtigste Felder:**
- `path` — Wo der ausführbare Host liegt
- `type: "stdio"` — Kommunikation über Standard-Ein/Ausgabe
- `allowed_origins` — Nur diese Extension-ID darf Nachrichten senden

Chrome sucht diese Dateien an festen Orten:
- Google Chrome: `~/.config/google-chrome/NativeMessagingHosts/`
- Chromium: `~/.config/chromium/NativeMessagingHosts/`
- Brave: `~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/`
- Vivaldi: `~/.config/vivaldi/NativeMessagingHosts/`
- Edge: `~/.config/microsoft-edge/NativeMessagingHosts/`
- Opera: `~/.config/opera/NativeMessagingHosts/`

### 3.2 Wrapper Script

Der Host ist ein Shell-Skript, das den eigentlichen Claude-Code-Prozess startet. Es fungiert als Übersetzer — empfängt die Anfragen von Chrome und leitet sie an Claude Code weiter.

**Pfad:** `~/.claude/chrome/chrome-native-host`

```sh
#!/bin/sh
exec "/home/smlflg/.local/share/claude/versions/2.1.49" --chrome-native-host
```

**Warum ein Wrapper?** 
Weil Chrome einen einfachen ausführbaren Pfad erwartet. Das Skript kann später auf eine neue Claude-Code-Version zeigen, ohne die JSON-Konfiguration zu ändern.

**Berechtigungen setzen:**
```bash
chmod +x ~/.claude/chrome/chrome-native-host
```

### 3.3 Chrome Extension

Die Extension lebt im Browser und führt die eigentlichen Aktionen aus. Sie ist der Agent vor Ort.

**Extension ID:** `fcoeoabgfenejglbffodgkkbkcdhcgfn`

Was sie kann:
- Tabs auflisten, erstellen, schließen
- Zu URLs navigieren
- DOM-Elemente finden und klicken
- Seiteninhalt lesen (Text, HTML)
- Screenshots machen
- JavaScript im Tab ausführen

Die Extension ist bereits in Chrome installiert — du musst sie nicht selbst hinzufügen.

### 3.4 stdio-Protokoll

So kommunizieren die Prozesse miteinander. Das NativeMessaging-Protokoll ist einfach, aber clever:

1. **Längen-Header** — Erst kommen 4 Bytes, die die Länge der Nachricht angeben (als 32-Bit Integer, Little-Endian)
2. **JSON-Body** — Danach folgt die eigentliche Nachricht als UTF-8-JSON

**Beispiel einer Anfrage (Hex-Dump):**
```
00 00 00 2C  {"action": "getTabs", "requestId": "abc123"}
```

Das sind 44 Bytes (0x2C) JSON. Chrome liest zuerst die 4 Bytes, weiß dann wie viele Bytes es als JSON lesen muss, und parst das Ergebnis.

**Beispiel-Antwort:**
```
00 00 00 5A  {"requestId": "abc123", "tabs": [{"id": 1, "title": "Amazon", "url": "https://..."}]}
```

---

## 4. Setup-Schritte

So aktivierst du die Chrome-Integration:

### Schritt 1: Claude Code starten

Im Terminal:
```bash
claude
# oder direkt mit Chrome-Modus:
claude --chrome
```

### Schritt 2: Chrome-Befehl aktivieren

Innerhalb von Claude Code:
```
/chrome
```

Das initialisiert die Verbindung und zeigt den Status der Extension.

### Schritt 3: Chrome komplett neustarten

**Wichtig:** Schließe ALLE Chrome-Fenster und -Prozesse. Das ist der häufigste Fehler — Chrome muss die Extension und den NativeMessaging Host neu laden.

```bash
# Alle Chrome-Prozesse beenden
pkill -f chrome
```

Dann Chrome neu starten.

### Schritt 4: Verifizieren

In Claude Code:
```
/chrome
```

Sollte jetzt den Status der Extension anzeigen (aktive Tabs, Extension-Version etc.).

---

## 5. Use Case im Keepa-Projekt

Warum ist das für dein Keepa-Projekt interessant? Hier kommt der praktische Nutzen:

### Das Problem

Die Keepa API hat Limits:
- Bestseller-Listen sind teuer oder begrenzt
- Category-Browsing liefert nicht alle Produkte
- Bestimmte Daten (z.B. aktuelle Rankings) sind nur auf der Amazon-Seite sichtbar

### Die Lösung

Statt die API zu quälen, holst du die Daten direkt vom Browser:

```
Claude Code Terminal
       │
       │ 1. Öffne Amazon Bestseller-Seite
       ▼
   Chrome Tab (Bestseller-Liste)
       │
       │ 2. Lese DOM → extrahiere ASINs
       ▼
   [B001H7G5D4, B08N5WRWNW, B09V3KXJPB, ...]
       │
       │ 3. Batch-Request an Keepa API
       ▼
   Keepa API → Produktdetails, Preise, Rankings
```

### Konkreter Workflow

1. `/chrome` → öffne neuen Tab mit Amazon-Bestseller-URL
2. Warte auf Laden (oder warte auf JavaScript-Rendering)
3. Führe JavaScript aus, das alle ASIN-Links aus dem DOM sammelt
4. Übergib die ASINs an Keepa API für Detail-Daten
5. Speichere in deiner Datenbank

**Vorteil gegenüber Selenium/Playwright:**
- Kein separater Browser-Treiber nötig
- Integriert in Claude Code — du steuerst alles aus einem Terminal
- Leichtgewichtiger als eine komplette Browser-Automatisierung

---

## 6. Konfigurierte Dateien auf diesem System

Hier sind alle relevanten Pfade auf deinem System:

| Komponente | Pfad | Zweck |
|------------|------|-------|
| NM Host JSON (Chrome) | `~/.config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json` | Chrome-Konfiguration |
| NM Host JSON (Chromium) | `~/.config/chromium/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json` | Chromium-Konfiguration |
| NM Host JSON (Brave) | `~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json` | Brave-Konfiguration |
| Wrapper Script | `~/.claude/chrome/chrome-native-host` | Startet Claude Code mit `--chrome-native-host` |
| Extension ID | `fcoeoabgfenejglbffodgkkbkcdhcgfn` | Die Chrome Extension |
| Claude Version | `2.1.49` | Aktuell verwendete Version |

**Alle Pfade existieren und sind konfiguriert.**

---

## 7. Troubleshooting

Hier sind die häufigsten Probleme und wie du sie löst:

### Extension nicht gefunden

**Symptom:** `/chrome` zeigt "Extension not found" oder "Connection failed"

**Lösung:** Chrome muss komplett neu gestartet werden
```bash
pkill -f chrome
# Dann Chrome neu starten
```

Ein "Neustart" einzelner Fenster reicht nicht — alle Prozesse müssen weg.

### NM Host JSON nicht gefunden

**Symptom:** Chrome meldet "Native host has exited"

**Lösung:** Prüfen, ob der Pfad in der JSON-Datei stimmt und das Script ausführbar ist
```bash
# Pfad in JSON prüfen
cat ~/.config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json

# Script ist executable?
ls -la ~/.claude/chrome/chrome-native-host
```

### Permission denied

**Symptom:** "Permission denied" beim Start des Native Messaging Host

**Lösung:**
```bash
chmod +x ~/.claude/chrome/chrome-native-host
```

### Verbindung bricht ab

**Symptom:** Befehle funktionieren, aber nach kurzer Zeit keine Antwort mehr

**Lösung:** Version im Wrapper-Script prüfen
```bash
# Aktuelle Claude-Version
claude --version

# Version im Wrapper-Script
cat ~/.claude/chrome/chrome-native-host
```

Die Pfade müssen übereinstimmen. Nach einem Claude-Code-Update zeigt das Script eventuell noch auf die alte Version.

### Nach Claude-Code-Update

**Symptom:** Extension hat nach Update aufgehört zu funktionieren

**Lösung:** Wrapper-Script zeigt auf alte Version
```bash
# Neue Version finden
ls -la ~/.local/share/claude/versions/

# Script anpassen (neue Version eintragen)
nano ~/.claude/chrome/chrome-native-host
```

Dann Chrome nochmal komplett neustarten.

---

## Schnellreferenz

**Befehle in Claude Code:**

| Befehl | Beschreibung |
|--------|--------------|
| `/chrome` | Chrome-Status anzeigen / Verbindung aktivieren |
| `/chrome tabs` | Alle offenen Tabs auflisten |
| `/chrome open <url>` | Neue Seite öffnen |
| `/chrome click <selector>` | Element im DOM klicken |
| `/chrome getHTML` | Seiten-HTML auslesen |

**Direkt im Code nutzen:**
```python
# Beispiel: Chrome-Tab steuern (sobald integriert)
result = await chrome.navigate("https://www.amazon.com/bestsellers")
asins = await chrome.eval("document.querySelectorAll('.a-link-emphasis')...")
```

---

*Letzte Aktualisierung: Februar 2026 | Claude Code 2.1.49 | Chrome 144*
