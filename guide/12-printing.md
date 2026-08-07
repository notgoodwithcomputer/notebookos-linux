# 12. Printing

Notebook OS prints to USB printers. There is no network, so network printers,
shared printers and cloud printing are not supported.

## How printing works

Applications do not communicate with the printer. An application renders its
document to a PDF, and the system submits that PDF to the print spooler. The
printed page therefore matches the exported PDF exactly, in every application
that offers both.

## Printer support

A printer is reached one of two ways.

**IPP over USB.** Printers made from roughly 2016 onward advertise IPP
Everywhere and convert pages to their own format themselves. The system sends
the PDF straight to the printer over its USB interface. No driver is involved,
and the printer's own page processing is used.

**Driver.** Older printers require the page to be converted into a page
description language first. The system includes:

| Driver set | Covers |
|---|---|
| Gutenprint | Approximately 5,000 inkjet and laser models |
| brlaser | Brother host-based lasers |
| splix | Samsung and Xerox host-based lasers |
| captdriver | Canon CAPT host-based lasers |

Settings chooses between the two routes when a printer is added. Applications
do not need to know which was used.

Host-based printers — inexpensive lasers that rely on the computer to build the
page — are covered by the second group. A printer that appears to accept a job
and then prints nothing is normally a printer that has been matched to the wrong
driver.

## Adding a printer

1. Connect the printer by USB and switch it on.
2. Open **Settings > Printers**.
3. Select **Find printers**.
4. Select the printer from the list.
5. Select **Add printer**.

The printer's name and the driver chosen for it are then shown.

| Control | Effect |
|---|---|
| Set default | Makes this the printer applications use unless told otherwise |
| Test page | Prints a test page. Use this to confirm the driver is correct before printing a real document. |
| Remove | Removes the printer |

## Printing a document

**File > Print…** in any application that produces a printable page.

The print dialog offers:

| Control | Effect |
|---|---|
| Printer | Which printer to use |
| Copies | How many copies to print |
| Print both sides | Available where the document is a booklet |

Selecting **Print** prepares the document and submits it.

## Exporting to PDF

Applications that can print can also export. **Export to PDF** writes the file
into `Documents` without asking anything; **Export…** opens a file picker
first. The two are different commands, distinguished by the ellipsis. See
[05. How applications work](05-how-applications-work.md).

A PDF exported this way can be copied to a USB stick and printed on another
computer, which is the route to take for a printer this system does not
support.

## Booklet printing

Novel and Screenplay can print a manuscript as a folded booklet. Pages are laid
out at half-letter size, two to a sheet, in saddle-stitch folding order.
Printing double-sided, folding the resulting stack down the middle and stapling
it produces a booklet with the pages in the correct sequence.

The booklet layout and the ordinary PDF export use the same renderer, so the two
outputs contain the same pages.

## When printing does not work

| Symptom | Cause |
|---|---|
| The printer does not appear under Find printers | The printer is off, not connected, or connected to a hub that is not powered. |
| Settings reports that printing is not available on this computer | The print system is not running. |
| The printer accepts the job and prints nothing | The wrong driver is in use. Remove the printer and add it again; if the same driver is selected, export to PDF and print from another computer. |
| The printer prints blank or garbled pages | As above — a driver mismatch. |
| Nothing prints and no error appears | Check that a printer has been set as the default. |
