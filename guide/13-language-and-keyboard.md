# 13. Language and keyboard

## Interface languages

The interface is available in 18 languages.

| Language | Shown as |
|---|---|
| English | English |
| German | Deutsch |
| Greek | Ελληνικά |
| Esperanto | Esperanto |
| Spanish | Español |
| French | Français |
| Hindi | हिन्दी |
| Italian | Italiano |
| Japanese | 日本語 |
| Korean | 한국어 |
| Dutch | Nederlands |
| Polish | Polski |
| Portuguese | Português |
| Russian | Русский |
| Serbo-Croatian | Srpskohrvatski |
| Turkish | Türkçe |
| Yiddish | ייִדיש |
| Simplified Chinese | 中文 |

Each language names itself in the picker, in its own script.

### Changing the language

**Settings > Region & Language > Language**.

The change takes effect as follows:

| Component | When it changes |
|---|---|
| Applications opened after the change | Immediately |
| Applications already open | Not until they are closed and reopened |
| The desktop and menu bar | At the next restart |

The Region & Language page states this.

### Right-to-left

Yiddish is written right to left. When it is selected, the whole interface is
mirrored: sidebars move to the right, alignment reverses, and controls appear in
the reverse order.

## Keyboard layouts

The layout is set in **Settings > Keyboard** or **Settings > Region & Language**.
Both pages offer the same list and both save the choice, so they cannot
disagree.

| Layout | Notes |
|---|---|
| US (QWERTY) | The default |
| Deutsch | |
| Esperanto | |
| Español | |
| Français | |
| हिन्दी / English | Two layouts |
| Italiano | |
| 日本語 (JIS) | Types ASCII directly |
| 日本語 (かな) | Kana; paired with US |
| 한국어 | Types ASCII directly |
| Nederlands | US QWERTY, which is what is used in practice in the Netherlands |
| Polski | |
| Português | |
| Русский / English | Two layouts |
| Srpskohrvatski | |
| Türkçe | |
| Ελληνικά / English | Two layouts |
| ייִדיש / English | Two layouts |

### Two-layout entries

Some entries name two layouts, for example "Русский / English". The first is
active when the computer starts, and `Alt+Shift` switches between them.

This applies to every script that cannot type ASCII. A password, a file name and
a search term are ASCII on this computer whatever language the interface is in,
so a layout that could not type them would make the computer unusable. Any
layout without a way to type ASCII is paired with a US layout automatically.

Japanese JIS and Korean layouts type ASCII directly and therefore stand alone.
The kana layout does not, so it is paired.

## Accented characters

Holding a letter key down opens a palette of that letter's accented forms over
the application.

```
hold "e"  ->  1 é   2 è   3 ê   4 ë   5 ē   6 ę   7 ė   8 ě   9 €
```

| Key | Action |
|---|---|
| `1`–`9` | Take that accent |
| `←` `→` | Move along the row |
| `Return` | Take the highlighted one |
| `Esc`, or any other key | Dismiss and keep the plain letter |

A tile can also be clicked.

The first press types the plain letter immediately; picking an accent replaces
it. Abandoning a hold therefore leaves the plain letter that was already typed.

Holding a letter that carries accents does not repeat it. Keys with no accents —
`Backspace`, the arrow keys, space and digits — repeat normally.

This works in every text field in every application.

## Chinese input

Chinese is typed with the built-in Pinyin input method.

| Key | Action |
|---|---|
| `Ctrl+Space` | Turns the input method on and off |
| *(type pinyin)* | A candidate list of characters appears, ordered by frequency |
| `1`–`9`, or `Space` | Choose a candidate |
| `-` `=`, or `↑` `↓` | Page through the candidates |
| `Backspace` | Edit the pinyin already typed |
| `Esc` | Cancel |

The Chinese layout uses a US keyboard as its base, because pinyin is typed in
Latin letters.

This works in every text field in every application.
