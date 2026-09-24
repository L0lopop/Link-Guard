<p align="center">
  <img src="assets/icon.png" width="128" alt="Link Guard">
</p>

<p align="center"><b>English</b> · <a href="README.ru.md">Русский</a></p>

# Link Guard

A plugin for **exteraGram** and **AyuGram** that checks a link before you open it and strips trackers out of it.

Links are analysed on your device. The scam site database arrives as a ready-made file, and nobody learns which links you open.

## What it does

**Checks a link before it opens.** If everything is fine, it stays out of your way. If not, it shows a breakdown and lets you decide.

| Trick | Example |
|---|---|
| Fake domain with `@` | `sberbank.ru@phish.top/login` actually leads to `phish.top` |
| Typos, including swapped letters | `gogole.com`, `sberbamk.ru` |
| Cyrillic letters posing as Latin | `sbеrbank.ru` with a Cyrillic «е» |
| Punycode | `xn--80ak6aa92e.com` is shown in readable form |
| Brand in a subdomain | `gosuslugi.ru.verify-account.cyou` |
| Brand name with extras | `sberbank-shop.ru`, `tinkoffshop.com`, `vtb-online.info` |
| Dangerous schemes | `javascript:`, `data:`, `file:`, `intent:` |
| Installer files | links to `.apk`, `.exe`, `.scr` |
| Other | an IP address instead of a domain, a non-standard port, scam-heavy zones, bait words |

**Knows scam sites by name.** A database built from eleven public lists — over 3 million addresses — is rebuilt in this repository every night and delivered as a ready-made file. Lookups happen on the device: no address ever leaves it.

**Knows freshly registered domains.** An address registered less than ten days ago that carries someone else's name is almost always a scam, and public lists will only catch it days later.

**Rates domain zones instead of relying on a hand-made list.** In `.digital` there are hundreds of scam sites for every known one, in `.xyz` more than a hundred. `.com` has the most scam sites by count, but next to the real ones they are a drop in the ocean, so `.com` stays quiet.

**Catches disguised link labels.** If a message labels a link as `sberbank.ru` while it leads somewhere else, the breakdown says so.

**Strips trackers.** Over 40 parameters by exact name (`fbclid`, `gclid`, `yclid`, `erid`, `msclkid` and more) plus the `utm_*`, `pk_*`, `hsa_*`, `mtm_*`, `matomo_*`, `piwik_*` prefixes. Works both when you open a link and in your outgoing messages. Everything else in the link stays untouched. Aggressive mode also removes `ref`, `si`, `spm`, `from` — it is off by default, because a few sites depend on them.

**Expands short links.** `bit.ly`, `clck.ru`, `vk.cc` and about thirty other services: shows the final address and the number of redirects, runs the final address through the full check and strips its trackers too.

**Two items in the message menu** — «Check links» (analyses every link at once, including those hidden under text) and «Copy without trackers».

**Knows where a link came from.** Links from a channel or a stranger get a stricter check, links from your contacts or «Saved Messages» a gentler one. Addresses on your home network are not treated as a threat.

**Can look up a domain's age.** A fresh registration plus an imitation of a well-known brand is almost always fraud. Off by default: the request goes to the third-party rdap.org service.

**Speaks English and Russian.** The interface follows your Telegram language, and you can pick one manually in the settings.

## How the database avoids false alarms

Public lists make mistakes regularly, so the database does not copy them blindly.

- **The 50,000 most visited sites** never get into the database, even if some list added `github.com` or `dropbox.com`.
- **The 200,000 most visited sites** are exempt from guesswork based on the zone, bait words or name length. That is why official short links of online stores such as `ali.click` do not look suspicious. If such a site does get into the database, the database wins over popularity.
- **Shared service addresses** such as `raw.githubusercontent.com`, `cdn.jsdelivr.net`, `storage.googleapis.com` are never flagged. Link lists name the host where a malicious file was found, but that host is shared by everyone.
- **Hosting platforms that hand out addresses to anyone** are recognised separately: a scam site on them is flagged by name, the platform itself is not.
- **A name glued to another word** counts as a fake only if that word is bait: `sberbankshop` is caught, `googlefonts` is not.

## Settings

The breakdown can appear on any finding or for every link. Trusted domains live in their own list: add them with a field and a button, remove them with a long press. In the dialog for a dangerous link the main button is «Cancel», so a stray tap never opens a phishing page.

Statistics show how many tags were removed and how many warnings were shown; tap the counters to reset them.

The «Updates» section has a manual update check and buttons to the source code and the plugin chat. The interface language is chosen at the very bottom.

## Installation

1. Download [`src/link_guard.plugin`](src/link_guard.plugin) or the latest version from the [releases](https://github.com/L0lopop/Link-Guard/releases/latest) page.
2. Send the file to yourself in «Saved Messages».
3. Tap it in the chat → «Install» → «Enable after installation».

Requires exteraGram or AyuGram 12.1.1 or newer. Older builds run the plugin engine in a reduced mode: long presses in settings are not supported there, and without them a trusted domain cannot be removed.

## What goes online

Link analysis is entirely local. The plugin goes online in four cases, and each one has its own switch in the settings:

- **expanding a short link** — a HEAD request to the link itself to learn the final address;
- **checking for updates** — every six hours it reads `update.json` from this repository; if you agree, it downloads the file and opens the standard install screen after making sure it really is Link Guard;
- **the scam site database** — every three hours it reads a build description of about a kilobyte from this repository's release and downloads the whole file only if the database has actually changed; which addresses you check is never reported to anyone;
- **domain age** — only if you turn it on: the domain being checked is sent to rdap.org.

Neither your chats nor your links are sent anywhere.

## Development

```bash
python tests/test_link_guard.py
python scripts/build_db.py && python tests/test_db_format.py db
```

The first suite replaces Android modules with stubs and runs the whole logic: link analysis, opening interception, cleaning outgoing messages, update checks, the database, both interface languages and compatibility with a reduced SDK. Accuracy is checked separately — a sample of ordinary links must pass without a single warning.

The second suite builds the real database from public lists and checks it on real data: that the format reads correctly, that a thousand popular sites raise no alarms, and that shared hosting platforms and shared service addresses are not blocked as a whole. The abuse.ch feeds need a free key in the `ABUSE_CH_KEY` variable; without it those two sources are skipped.

Database sources: [HaGeZi](https://github.com/hagezi/dns-blocklists), [Phishing Army](https://phishing.army), [Phishing.Database](https://github.com/Phishing-Database/Phishing.Database), [CERT.PL](https://cert.pl), [The Block List Project](https://github.com/blocklistproject/Lists), [malware-filter](https://gitlab.com/malware-filter), [phishunt](https://phishunt.io), [URLhaus](https://urlhaus.abuse.ch) and [ThreatFox](https://threatfox.abuse.ch). Freshly registered domains come from [cenk/nrd](https://github.com/cenk/nrd), popular sites from [Tranco](https://tranco-list.eu), domain splitting from the [Public Suffix List](https://publicsuffix.org).

## Contact

News, questions and ideas — in the plugin chat: [t.me/kringplugins](https://t.me/kringplugins).

## License

MIT — see [LICENSE](LICENSE).
