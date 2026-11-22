DONUT CROWN - v10

Neu / gefixt in dieser Version:
- Tickets: oeffnen sich nicht mehr doppelt (sofortiges defer + Sperre pro Nutzer). Der Bot startet ausserdem
  nicht mehr, wenn schon eine zweite Kopie auf demselben PC laeuft (zwei Kopien = jede Aktion doppelt).
- /lock + /unlock: Absturz behoben (PermissionOverwrite.copy gibt es nicht). Sperrt jetzt @everyone plus alle
  Rollen/Mitglieder mit ausdruecklicher Schreib-Erlaubnis im Channel. Der Bot braucht Manage Roles + Manage Channels
  (oder Administrator). Alte gespeicherte Locks aus v9 lassen sich weiter mit /unlock aufheben.
- /post: TikTok-Erkennung neu. Normale Links, vm./vt.-Kurzlinks, /t/-Links, Foto-Posts, Links mit Tracking-Parametern
  und Links mitten in einem Text werden erkannt. Der alte Regex war doppelt escaped und hat praktisch nie gematcht.
  Fallback auf die Seiten-Metadaten, wenn TikTok oEmbed nichts liefert.
- /unban: gleicher Escape-Fehler behoben (User-Mentions wurden nicht erkannt, "\n" stand woertlich in der Nachricht).
- Fehlermeldungen: Admins sehen bei "Command Error" jetzt den Fehlertyp.

Neu: /welcome setup
- Dashboard: Channel waehlen, Nachricht/Titel/Footer bearbeiten, Embed-Farbe (HEX), Info-Felder an/aus, Ping an/aus,
  Preview, Aktivieren/Deaktivieren.
- Platzhalter: {user} {username} {name} {server} {count} {id}
- /welcome test sendet eine Test-Nachricht in den Welcome-Channel.
- WICHTIG: Im Discord Developer Portal muss "Server Members Intent" aktiviert sein, sonst bekommt der Bot keine Join-Events.

Installation:
- Bot stoppen, alte bot.py sichern, bot.py ersetzen. Deine .env und config.json behalten.
  py -m pip install -r requirements.txt
  py bot.py
