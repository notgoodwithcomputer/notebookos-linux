/*
 * ippusb — driverless printing over USB for Notebook OS.
 *
 * WHY THIS EXISTS
 * ---------------
 * Notebook OS ships a large classic-driver library (Gutenprint, brlaser, splix,
 * captdriver). That library covers office lasers and printers built up to
 * roughly 2010. It does not cover most printers sold since, because the
 * industry stopped writing per-model drivers: modern printers instead advertise
 * IPP Everywhere / AirPrint / Mopria and rasterise internally, so the host
 * sends them a PDF or PWG-Raster and no driver is involved at all.
 *
 * Handed a classic driver it does not speak, such a printer accepts the job,
 * lights up "Receiving Data", discards it, and prints nothing — which is
 * indistinguishable from the OS being broken. That was the reported bug, and it
 * is not model-specific: it is what happens to every printer newer than the
 * driver library.
 *
 * The usual answer on Linux is the ipp-usb daemon, which proxies a localhost
 * TCP port onto the printer's USB pipe so the ordinary `ipp` backend can reach
 * it. That is impossible here: this kernel is the no-internet fork and net/ipv4
 * is deleted outright, so there is no TCP at all, not even loopback.
 *
 * But IPP does not actually need TCP. The IPP-USB protocol (USB printer class,
 * bInterfaceClass 7 / bInterfaceSubClass 1 / bInterfaceProtocol 4) is plain
 * HTTP/1.1 carried on a pair of USB bulk endpoints. So this backend speaks
 * HTTP and IPP directly over libusb, with no socket of any kind, and the queue
 * gets its PPD from the printer's own advertised attributes via CUPS's IPP
 * Everywhere PPD generator. The result is a driverless queue for any printer
 * made in roughly the last decade, of any brand.
 *
 * MODES
 *   ippusb                          CUPS backend: list devices
 *   ippusb job user title n opts [f] CUPS backend: print
 *   ippusb --ppd URI OUT.ppd        generate an IPP Everywhere PPD
 *   ippusb --attrs URI              dump printer attributes (support tool)
 *   ippusb --selftest               internal checks, no hardware needed
 *
 * The transport is behind chan_*, and IPPUSB_MOCK=<unix-socket> swaps USB for a
 * socket so every layer above the wire — HTTP framing, IPP encode/decode, PPD
 * generation, option mapping, the CUPS backend contract — is testable on a
 * build host with no printer attached.
 */

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <libusb-1.0/libusb.h>

#include <cups/backend.h>
#include <cups/cups.h>
#include <cups/http.h>
#include <cups/ipp.h>
#include <cups/language.h>

/*
 * Private but exported by libcups: builds an IPP Everywhere PPD from a
 * Get-Printer-Attributes response. This is the same generator `lpadmin -m
 * everywhere` uses, so the PPDs this backend produces are the ones CUPS itself
 * would have produced had it been able to reach the printer over IP.
 */
extern char *_ppdCreateFromIPP2(char *buffer, size_t bufsize, ipp_t *response,
                                cups_lang_t *lang);

#define IPPUSB_CLASS    7       /* USB printer class */
#define IPPUSB_SUBCLASS 1       /* printer */
#define IPPUSB_PROTOCOL 4       /* IPP-USB */

#define BULK_TIMEOUT_MS   30000 /* one bulk transfer */
#define WRITE_STALL_LIMIT 8     /* consecutive empty write timeouts tolerated */
#define REPLY_DEADLINE_S  120   /* whole HTTP response */
#define JOB_POLL_MAX_S    900   /* how long to watch a job print */

static volatile sig_atomic_t g_cancelled = 0;

static void on_term(int sig) { (void)sig; g_cancelled = 1; }

/* CUPS backend logging contract: everything goes to stderr with a level. */
static void logmsg(const char *level, const char *fmt, ...)
{
  va_list ap;
  fprintf(stderr, "%s: ", level);
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);
  fputc('\n', stderr);
  fflush(stderr);
}

#define DBG(...)  logmsg("DEBUG", __VA_ARGS__)
#define INF(...)  logmsg("INFO", __VA_ARGS__)
#define ERR(...)  logmsg("ERROR", __VA_ARGS__)


/* ------------------------------------------------------------------ buffer */

typedef struct
{
  unsigned char *d;
  size_t         n;             /* bytes used */
  size_t         cap;
} buf_t;

static void buf_init(buf_t *b) { b->d = NULL; b->n = b->cap = 0; }
static void buf_free(buf_t *b) { free(b->d); buf_init(b); }

static int buf_add(buf_t *b, const void *p, size_t n)
{
  if (b->n + n + 1 > b->cap)
  {
    size_t cap = b->cap ? b->cap : 4096;
    while (cap < b->n + n + 1)
      cap *= 2;
    unsigned char *d = realloc(b->d, cap);
    if (!d)
      return (-1);
    b->d   = d;
    b->cap = cap;
  }
  memcpy(b->d + b->n, p, n);
  b->n += n;
  b->d[b->n] = '\0';            /* so a buffer is always safe to treat as text */
  return (0);
}

static int buf_addf(buf_t *b, const char *fmt, ...)
{
  char    tmp[1024];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
  va_end(ap);
  if (n < 0 || (size_t)n >= sizeof(tmp))
    return (-1);
  return (buf_add(b, tmp, (size_t)n));
}

/* Drop the first n bytes. Used as the HTTP reader consumes its buffer. */
static void buf_drop(buf_t *b, size_t n)
{
  if (n >= b->n)
  {
    b->n = 0;
  }
  else
  {
    memmove(b->d, b->d + n, b->n - n);
    b->n -= n;
  }
  if (b->d)
    b->d[b->n] = '\0';
}


/* --------------------------------------------------------------- transport */

typedef struct
{
  libusb_device_handle *h;      /* NULL in mock mode */
  int                   iface;
  int                   altset;
  unsigned char         ep_in;
  unsigned char         ep_out;
  int                   mock_fd; /* -1 unless IPPUSB_MOCK */
  char                  path[64]; /* IPP resource path that answered */
} chan_t;

static void chan_close(chan_t *c)
{
  if (c->mock_fd >= 0)
  {
    close(c->mock_fd);
    c->mock_fd = -1;
  }
  if (c->h)
  {
    libusb_release_interface(c->h, c->iface);
    libusb_close(c->h);
    c->h = NULL;
  }
}

static int chan_write(chan_t *c, const void *p, size_t n)
{
  const unsigned char *d = p;
  int                  stalls = 0;

  while (n > 0)
  {
    if (c->mock_fd >= 0)
    {
      ssize_t w = write(c->mock_fd, d, n);
      if (w <= 0)
      {
        if (w < 0 && errno == EINTR)
          continue;
        return (-1);
      }
      d += w;
      n -= (size_t)w;
      continue;
    }

    int sent = 0;
    int chunk = (n > 65536) ? 65536 : (int)n;
    int rc = libusb_bulk_transfer(c->h, c->ep_out, (unsigned char *)d, chunk,
                                  &sent, BULK_TIMEOUT_MS);
    if (rc == LIBUSB_ERROR_TIMEOUT && sent > 0)
      rc = 0;                   /* partial progress is still progress */
    if (rc == LIBUSB_ERROR_TIMEOUT)
    {
      /* A printer that is mid-page stops draining the pipe until it has
       * somewhere to put the next band. That is normal on a long job, not a
       * failure, so wait it out for a few rounds before giving up. */
      if (++stalls <= WRITE_STALL_LIMIT && !g_cancelled)
      {
        DBG("printer is not reading yet (%d)", stalls);
        continue;
      }
      ERR("The printer stopped accepting data.");
      return (-1);
    }
    if (rc < 0)
    {
      ERR("USB write failed: %s", libusb_strerror(rc));
      return (-1);
    }
    if (sent <= 0)
      return (-1);
    stalls = 0;
    d += sent;
    n -= (size_t)sent;
  }
  return (0);
}

/*
 * One read. Returns bytes read, 0 on timeout with nothing available, -1 on a
 * hard error. A bulk IN that times out empty is normal while the printer
 * thinks, so the caller loops against a wall-clock deadline instead.
 */
static int chan_read(chan_t *c, void *p, size_t n)
{
  if (c->mock_fd >= 0)
  {
    ssize_t r = read(c->mock_fd, p, n);
    if (r < 0 && errno == EINTR)
      return (0);
    /* On a stream socket 0 means the far end is gone. On a USB bulk pipe the
     * same 0 is just an empty packet, so the two must not share a meaning:
     * treating EOF as "nothing yet" would spin here until the deadline. */
    return (r <= 0 ? -1 : (int)r);
  }

  int got = 0;
  int rc  = libusb_bulk_transfer(c->h, c->ep_in, p, (int)n, &got,
                                 BULK_TIMEOUT_MS);
  if (rc == LIBUSB_ERROR_TIMEOUT)
    return (got);
  if (rc < 0)
  {
    ERR("USB read failed: %s", libusb_strerror(rc));
    return (-1);
  }
  return (got);
}


/* -------------------------------------------------------------------- HTTP */

/*
 * Read one complete HTTP response. `pending` carries bytes already read past
 * the previous response so a keep-alive connection stays in sync.
 *
 * Returns the HTTP status, or -1. The entity body lands in `body`.
 */
static int http_read_response(chan_t *c, buf_t *pending, buf_t *body)
{
  time_t deadline = time(NULL) + REPLY_DEADLINE_S;
  char   tmp[8192];

  buf_free(body);
  buf_init(body);

again:
  /* headers */
  char *hdr_end = NULL;
  while (!(hdr_end = pending->n ? strstr((char *)pending->d, "\r\n\r\n") : NULL))
  {
    if (time(NULL) > deadline)
    {
      ERR("The printer did not answer.");
      return (-1);
    }
    if (g_cancelled)
      return (-1);
    int got = chan_read(c, tmp, sizeof(tmp));
    if (got < 0)
      return (-1);
    if (got > 0 && buf_add(pending, tmp, (size_t)got) < 0)
      return (-1);
  }

  size_t hdr_len = (size_t)(hdr_end - (char *)pending->d) + 4;
  char  *head    = malloc(hdr_len + 1);
  if (!head)
    return (-1);
  memcpy(head, pending->d, hdr_len);
  head[hdr_len] = '\0';
  buf_drop(pending, hdr_len);

  int status = 0;
  if (sscanf(head, "HTTP/%*d.%*d %d", &status) != 1)
  {
    ERR("The printer sent a reply that is not HTTP.");
    free(head);
    return (-1);
  }

  /* A 1xx is informational; the real response follows it. */
  if (status >= 100 && status < 200)
  {
    free(head);
    goto again;
  }

  /* Only Content-Length and chunked exist in practice; handle both. */
  long long clen    = -1;
  int       chunked = 0;
  for (char *ln = strchr(head, '\n'); ln; ln = strchr(ln, '\n'))
  {
    ln++;
    if (!strncasecmp(ln, "Content-Length:", 15))
      clen = strtoll(ln + 15, NULL, 10);
    else if (!strncasecmp(ln, "Transfer-Encoding:", 18) &&
             strcasestr(ln, "chunked"))
      chunked = 1;
  }
  free(head);

  if (chunked)
  {
    for (;;)
    {
      /* one chunk header line */
      char *nl;
      while (!(nl = pending->n ? strstr((char *)pending->d, "\r\n") : NULL))
      {
        if (time(NULL) > deadline || g_cancelled)
          return (-1);
        int got = chan_read(c, tmp, sizeof(tmp));
        if (got < 0)
          return (-1);
        if (got > 0 && buf_add(pending, tmp, (size_t)got) < 0)
          return (-1);
      }
      long long sz = strtoll((char *)pending->d, NULL, 16);
      buf_drop(pending, (size_t)(nl - (char *)pending->d) + 2);
      if (sz <= 0)
      {
        /* trailer + final CRLF */
        while (pending->n < 2)
        {
          if (time(NULL) > deadline || g_cancelled)
            break;
          int got = chan_read(c, tmp, sizeof(tmp));
          if (got <= 0)
            break;
          buf_add(pending, tmp, (size_t)got);
        }
        if (pending->n >= 2)
          buf_drop(pending, 2);
        break;
      }
      while ((long long)pending->n < sz + 2)
      {
        if (time(NULL) > deadline || g_cancelled)
          return (-1);
        int got = chan_read(c, tmp, sizeof(tmp));
        if (got < 0)
          return (-1);
        if (got > 0 && buf_add(pending, tmp, (size_t)got) < 0)
          return (-1);
      }
      if (buf_add(body, pending->d, (size_t)sz) < 0)
        return (-1);
      buf_drop(pending, (size_t)sz + 2);
    }
  }
  else if (clen > 0)
  {
    while ((long long)pending->n < clen)
    {
      if (time(NULL) > deadline || g_cancelled)
        return (-1);
      int got = chan_read(c, tmp, sizeof(tmp));
      if (got < 0)
        return (-1);
      if (got > 0 && buf_add(pending, tmp, (size_t)got) < 0)
        return (-1);
    }
    if (buf_add(body, pending->d, (size_t)clen) < 0)
      return (-1);
    buf_drop(pending, (size_t)clen);
  }

  return (status);
}

/*
 * POST an IPP message, optionally followed by a document, and read the reply.
 *
 * Content-Length is always used rather than chunked encoding: the total size is
 * knowable in every case here (the document is a real file — the caller spools
 * stdin first), and a plain Content-Length body is the one framing every HTTP
 * implementation in a printer is certain to accept.
 */
static int ipp_transact(chan_t *c, buf_t *pending, const void *ippmsg,
                        size_t ipplen, int doc_fd, long long doc_len,
                        buf_t *reply)
{
  buf_t hdr;
  buf_init(&hdr);
  buf_addf(&hdr, "POST %s HTTP/1.1\r\n", c->path);
  buf_addf(&hdr, "Host: localhost\r\n");
  buf_addf(&hdr, "User-Agent: NotebookOS-ippusb/1.0\r\n");
  buf_addf(&hdr, "Content-Type: application/ipp\r\n");
  buf_addf(&hdr, "Accept: application/ipp\r\n");
  buf_addf(&hdr, "Connection: keep-alive\r\n");
  buf_addf(&hdr, "Content-Length: %lld\r\n\r\n",
           (long long)ipplen + (doc_len > 0 ? doc_len : 0));

  int rc = chan_write(c, hdr.d, hdr.n);
  buf_free(&hdr);
  if (rc < 0)
    return (-1);
  if (chan_write(c, ippmsg, ipplen) < 0)
    return (-1);

  if (doc_fd >= 0 && doc_len > 0)
  {
    char      chunk[65536];
    long long left = doc_len;
    while (left > 0)
    {
      if (g_cancelled)
        return (-1);
      size_t  want = (left > (long long)sizeof(chunk)) ? sizeof(chunk)
                                                       : (size_t)left;
      ssize_t got  = read(doc_fd, chunk, want);
      if (got <= 0)
      {
        if (got < 0 && errno == EINTR)
          continue;
        ERR("The document could not be read while sending it.");
        return (-1);
      }
      if (chan_write(c, chunk, (size_t)got) < 0)
        return (-1);
      left -= got;
    }
  }

  return (http_read_response(c, pending, reply));
}


/* ------------------------------------------------------------- IPP helpers */

static ssize_t ipp_write_cb(void *ctx, ipp_uchar_t *buffer, size_t bytes)
{
  return (buf_add((buf_t *)ctx, buffer, bytes) < 0 ? -1 : (ssize_t)bytes);
}

typedef struct
{
  const unsigned char *d;
  size_t               n;
  size_t               pos;
} rdctx_t;

static ssize_t ipp_read_cb(void *ctx, ipp_uchar_t *buffer, size_t bytes)
{
  rdctx_t *r = ctx;
  size_t   avail = r->n - r->pos;
  if (bytes > avail)
    bytes = avail;
  if (bytes)
    memcpy(buffer, r->d + r->pos, bytes);
  r->pos += bytes;
  return ((ssize_t)bytes);
}

static int ipp_serialize(ipp_t *msg, buf_t *out)
{
  ipp_state_t st;
  ippSetState(msg, IPP_STATE_IDLE);
  while ((st = ippWriteIO(out, ipp_write_cb, 1, NULL, msg)) != IPP_STATE_DATA)
    if (st == IPP_STATE_ERROR)
      return (-1);
  return (0);
}

static ipp_t *ipp_parse(const buf_t *in)
{
  rdctx_t     rd  = { in->d, in->n, 0 };
  ipp_t      *msg = ippNew();
  ipp_state_t st;

  if (!msg)
    return (NULL);
  ippSetState(msg, IPP_STATE_IDLE);
  while ((st = ippReadIO(&rd, ipp_read_cb, 1, NULL, msg)) != IPP_STATE_DATA)
  {
    if (st == IPP_STATE_ERROR)
    {
      ippDelete(msg);
      return (NULL);
    }
  }
  return (msg);
}

static ipp_t *new_request(ipp_op_t op, const char *path, const char *user)
{
  char uri[256];
  ipp_t *req = ippNewRequest(op);

  if (!req)
    return (NULL);
  ippSetVersion(req, 2, 0);
  snprintf(uri, sizeof(uri), "ipp://localhost%s", path);
  ippAddString(req, IPP_TAG_OPERATION, IPP_TAG_URI, "printer-uri", NULL, uri);
  if (user && *user)
    ippAddString(req, IPP_TAG_OPERATION, IPP_TAG_NAME, "requesting-user-name",
                 NULL, user);
  return (req);
}

/* True if `value` appears in the multi-valued keyword/mimetype attribute. */
static int attr_has(ipp_t *resp, const char *name, const char *value)
{
  ipp_attribute_t *a = ippFindAttribute(resp, name, IPP_TAG_ZERO);
  int              i, n;

  if (!a)
    return (0);
  for (i = 0, n = ippGetCount(a); i < n; i++)
  {
    const char *s = ippGetString(a, i, NULL);
    if (s && !strcasecmp(s, value))
      return (1);
  }
  return (0);
}


/* --------------------------------------------------- printer attribute read */

/*
 * IPP-USB does not standardise the resource path. /ipp/print is overwhelmingly
 * the common one, but enough printers answer only on a vendor path that probing
 * is worth three extra round trips on a cable that has no latency to speak of.
 */
static const char * const IPP_PATHS[] = {
  "/ipp/print", "/ipp/printer", "/ipp", "/", NULL
};

static ipp_t *get_printer_attrs(chan_t *c, buf_t *pending)
{
  static const char * const want[] = {
    "all", "media-col-database"
  };

  for (int p = 0; IPP_PATHS[p]; p++)
  {
    /* Once a path has answered, stop probing and reuse it. */
    if (c->path[0] && strcmp(c->path, IPP_PATHS[p]))
      continue;
    if (!c->path[0])
      snprintf(c->path, sizeof(c->path), "%s", IPP_PATHS[p]);

    ipp_t *req = new_request(IPP_OP_GET_PRINTER_ATTRIBUTES, c->path, NULL);
    if (!req)
      return (NULL);
    ippAddStrings(req, IPP_TAG_OPERATION, IPP_TAG_KEYWORD,
                  "requested-attributes", 2, NULL, want);

    buf_t msg, reply;
    buf_init(&msg);
    buf_init(&reply);
    if (ipp_serialize(req, &msg) < 0)
    {
      ippDelete(req);
      buf_free(&msg);
      return (NULL);
    }
    ippDelete(req);

    int http = ipp_transact(c, pending, msg.d, msg.n, -1, 0, &reply);
    buf_free(&msg);

    if (http == 200 && reply.n > 0)
    {
      ipp_t *resp = ipp_parse(&reply);
      buf_free(&reply);
      if (resp && ippGetStatusCode(resp) < IPP_STATUS_ERROR_BAD_REQUEST)
      {
        DBG("IPP resource path is %s", c->path);
        return (resp);
      }
      if (resp)
        ippDelete(resp);
    }
    else
    {
      buf_free(&reply);
    }

    DBG("no IPP service on %s (HTTP %d)", c->path, http);
    c->path[0] = '\0';
    /* A dead transport is not going to answer on another path either. */
    if (http < 0)
      break;
  }

  return (NULL);
}


/* --------------------------------------------------------------- discovery */

typedef struct
{
  char mfg[128];
  char mdl[192];
  char serial[128];
  char devid[1024];
  int  iface;
  int  altset;
  unsigned char ep_in, ep_out;
  uint8_t bus, addr;
} found_t;

static void trim(char *s)
{
  size_t n = strlen(s);
  while (n && isspace((unsigned char)s[n - 1]))
    s[--n] = '\0';
  size_t i = 0;
  while (s[i] && isspace((unsigned char)s[i]))
    i++;
  if (i)
    memmove(s, s + i, strlen(s + i) + 1);
}

/* Pull one key out of an IEEE-1284 device ID string. */
static void devid_field(const char *devid, const char *k1, const char *k2,
                        char *out, size_t outsz)
{
  const char *keys[2] = { k1, k2 };

  out[0] = '\0';
  for (int k = 0; k < 2; k++)
  {
    if (!keys[k])
      continue;
    size_t klen = strlen(keys[k]);
    for (const char *p = devid; p && *p; )
    {
      while (*p == ';' || *p == ' ')
        p++;
      if (!strncasecmp(p, keys[k], klen) && p[klen] == ':')
      {
        const char *v   = p + klen + 1;
        const char *end = strchr(v, ';');
        size_t      len = end ? (size_t)(end - v) : strlen(v);
        if (len >= outsz)
          len = outsz - 1;
        memcpy(out, v, len);
        out[len] = '\0';
        trim(out);
        return;
      }
      p = strchr(p, ';');
      if (p)
        p++;
    }
  }
}

static void get_string_desc(libusb_device_handle *h, uint8_t idx, char *out,
                            size_t outsz)
{
  out[0] = '\0';
  if (!idx)
    return;
  if (libusb_get_string_descriptor_ascii(h, idx, (unsigned char *)out,
                                         (int)outsz) < 0)
    out[0] = '\0';
  trim(out);
}

/*
 * Class-specific GET_DEVICE_ID. The reply is a 2-byte big-endian length
 * followed by the IEEE-1284 string, and the length includes those two bytes.
 */
static void get_device_id(libusb_device_handle *h, int cfg, int iface,
                          int altset, char *out, size_t outsz)
{
  unsigned char raw[1024];

  out[0] = '\0';
  int n = libusb_control_transfer(h,
                                  LIBUSB_ENDPOINT_IN | LIBUSB_REQUEST_TYPE_CLASS |
                                  LIBUSB_RECIPIENT_INTERFACE,
                                  0 /* GET_DEVICE_ID */, (uint16_t)cfg,
                                  (uint16_t)((iface << 8) | altset),
                                  raw, sizeof(raw), 5000);
  if (n < 3)
    return;
  size_t len = ((size_t)raw[0] << 8) | raw[1];
  if (len < 2 || len > (size_t)n)
    len = (size_t)n;
  len -= 2;
  if (len >= outsz)
    len = outsz - 1;
  memcpy(out, raw + 2, len);
  out[len] = '\0';
  trim(out);
}

/*
 * Walk every configuration/interface/altsetting looking for IPP-USB. Some
 * printers hide it as an ALTERNATE SETTING of an interface whose setting 0 is
 * an ordinary 7/1/2 printer port, so scanning altsettings (not just the active
 * one) is what makes those devices visible at all.
 */
static int find_ippusb_iface(libusb_device *dev, int *out_iface, int *out_alt,
                             unsigned char *out_in, unsigned char *out_out)
{
  struct libusb_config_descriptor *cfg = NULL;

  if (libusb_get_active_config_descriptor(dev, &cfg) < 0 || !cfg)
    return (0);

  int found = 0;
  for (int i = 0; i < cfg->bNumInterfaces && !found; i++)
  {
    const struct libusb_interface *itf = &cfg->interface[i];
    for (int a = 0; a < itf->num_altsetting && !found; a++)
    {
      const struct libusb_interface_descriptor *d = &itf->altsetting[a];
      if (d->bInterfaceClass != IPPUSB_CLASS ||
          d->bInterfaceSubClass != IPPUSB_SUBCLASS ||
          d->bInterfaceProtocol != IPPUSB_PROTOCOL)
        continue;

      unsigned char ep_in = 0, ep_out = 0;
      for (int e = 0; e < d->bNumEndpoints; e++)
      {
        const struct libusb_endpoint_descriptor *ep = &d->endpoint[e];
        if ((ep->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK) !=
            LIBUSB_TRANSFER_TYPE_BULK)
          continue;
        if (ep->bEndpointAddress & LIBUSB_ENDPOINT_IN)
        {
          if (!ep_in)
            ep_in = ep->bEndpointAddress;
        }
        else if (!ep_out)
        {
          ep_out = ep->bEndpointAddress;
        }
      }
      if (!ep_in || !ep_out)
        continue;

      *out_iface = d->bInterfaceNumber;
      *out_alt   = d->bAlternateSetting;
      *out_in    = ep_in;
      *out_out   = ep_out;
      found      = 1;
    }
  }

  libusb_free_config_descriptor(cfg);
  return (found);
}

/* Fill in identity for one candidate device. Returns 0 on success. */
static int describe(libusb_device *dev, found_t *f)
{
  struct libusb_device_descriptor dd;
  libusb_device_handle           *h = NULL;

  memset(f, 0, sizeof(*f));
  if (libusb_get_device_descriptor(dev, &dd) < 0)
    return (-1);
  if (!find_ippusb_iface(dev, &f->iface, &f->altset, &f->ep_in, &f->ep_out))
    return (-1);
  if (libusb_open(dev, &h) < 0)
    return (-1);

  get_string_desc(h, dd.iManufacturer, f->mfg, sizeof(f->mfg));
  get_string_desc(h, dd.iProduct, f->mdl, sizeof(f->mdl));
  get_string_desc(h, dd.iSerialNumber, f->serial, sizeof(f->serial));

  struct libusb_config_descriptor *cfg = NULL;
  int cfgval = 0;
  if (libusb_get_active_config_descriptor(dev, &cfg) == 0 && cfg)
  {
    cfgval = cfg->bConfigurationValue;
    libusb_free_config_descriptor(cfg);
  }
  get_device_id(h, cfgval, f->iface, f->altset, f->devid, sizeof(f->devid));
  libusb_close(h);

  /* The string descriptors are the friendlier names, but plenty of printers
   * leave them blank or generic, so fall back to the 1284 ID. */
  if (!f->mfg[0])
    devid_field(f->devid, "MANUFACTURER", "MFG", f->mfg, sizeof(f->mfg));
  if (!f->mdl[0])
    devid_field(f->devid, "MODEL", "MDL", f->mdl, sizeof(f->mdl));
  if (!f->serial[0])
    devid_field(f->devid, "SERIALNUMBER", "SN", f->serial, sizeof(f->serial));
  if (!f->mfg[0])
    snprintf(f->mfg, sizeof(f->mfg), "%04x", dd.idVendor);
  if (!f->mdl[0])
    snprintf(f->mdl, sizeof(f->mdl), "%04x", dd.idProduct);

  /* Printers habitually repeat the brand in the product string ("Brother
   * Brother MFC-J1355DW"); one brand in the name is enough. */
  size_t mfglen = strlen(f->mfg);
  if (mfglen && !strncasecmp(f->mdl, f->mfg, mfglen) &&
      isspace((unsigned char)f->mdl[mfglen]))
    memmove(f->mdl, f->mdl + mfglen + 1, strlen(f->mdl + mfglen + 1) + 1);

  f->bus  = libusb_get_bus_number(dev);
  f->addr = libusb_get_device_address(dev);
  return (0);
}

static void make_uri(const found_t *f, char *uri, size_t urisz)
{
  char opts[256] = "";

  if (f->serial[0])
    snprintf(opts, sizeof(opts), "?serial=%s", f->serial);
  httpAssembleURIf(HTTP_URI_CODING_ALL, uri, (int)urisz, "ippusb", NULL,
                   f->mfg, 0, "/%s%s", f->mdl, opts);
}

/* Enumerate. cb returns non-zero to stop. */
static int enumerate(int (*cb)(const found_t *, libusb_device *, void *),
                     void *ctx)
{
  libusb_device **list = NULL;
  ssize_t         n;
  int             stopped = 0;

  if (libusb_init(NULL) < 0)
  {
    ERR("USB is unavailable on this machine.");
    return (-1);
  }
  n = libusb_get_device_list(NULL, &list);
  for (ssize_t i = 0; i < n && !stopped; i++)
  {
    found_t f;
    if (describe(list[i], &f) == 0)
      stopped = cb(&f, list[i], ctx);
  }
  if (list)
    libusb_free_device_list(list, 1);
  return (stopped);
}


/* ------------------------------------------------------------ open by URI */

typedef struct
{
  const char *want_mfg;
  const char *want_mdl;
  const char *want_serial;
  chan_t     *chan;
  int         opened;
} openctx_t;

static int open_cb(const found_t *f, libusb_device *dev, void *vctx)
{
  openctx_t *o = vctx;

  if (o->want_mfg && *o->want_mfg && strcasecmp(o->want_mfg, f->mfg))
    return (0);
  if (o->want_mdl && *o->want_mdl && strcasecmp(o->want_mdl, f->mdl))
    return (0);
  /* A serial in the URI must match if the printer reports one; a printer that
   * reports none can still be opened by make and model alone. */
  if (o->want_serial && *o->want_serial && f->serial[0] &&
      strcasecmp(o->want_serial, f->serial))
    return (0);

  libusb_device_handle *h = NULL;
  if (libusb_open(dev, &h) < 0)
  {
    ERR("The printer could not be opened. Unplug it and plug it back in.");
    return (0);
  }

  /* usblp will already own the interface; let libusb take it and hand it back
   * automatically when we are done. */
  libusb_set_auto_detach_kernel_driver(h, 1);

  if (libusb_claim_interface(h, f->iface) < 0)
  {
    ERR("The printer is in use by something else.");
    libusb_close(h);
    return (0);
  }
  if (f->altset != 0 &&
      libusb_set_interface_alt_setting(h, f->iface, f->altset) < 0)
  {
    ERR("The printer's IPP interface could not be selected.");
    libusb_release_interface(h, f->iface);
    libusb_close(h);
    return (0);
  }

  /*
   * Start the session from a known state. A previous job that was cancelled
   * part-way leaves the endpoint's data toggle where it stopped, and the first
   * read of this session then returns the tail of that one — which parses as
   * neither HTTP nor IPP. CLEAR_FEATURE(ENDPOINT_HALT) resets the toggle and
   * is a no-op on an endpoint that is not halted.
   */
  libusb_clear_halt(h, f->ep_in);
  libusb_clear_halt(h, f->ep_out);

  o->chan->h       = h;
  o->chan->iface   = f->iface;
  o->chan->altset  = f->altset;
  o->chan->ep_in   = f->ep_in;
  o->chan->ep_out  = f->ep_out;
  o->chan->mock_fd = -1;
  o->opened        = 1;
  return (1);
}

/*
 * Connect to the mock printer instead of a real one. Used by the test harness
 * so every layer above libusb runs for real on a machine with no printer.
 */
static int chan_open_mock(chan_t *c, const char *path)
{
  struct sockaddr_un sa;
  int fd = socket(AF_UNIX, SOCK_STREAM, 0);

  if (fd < 0)
    return (-1);
  memset(&sa, 0, sizeof(sa));
  sa.sun_family = AF_UNIX;
  snprintf(sa.sun_path, sizeof(sa.sun_path), "%s", path);
  if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0)
  {
    close(fd);
    return (-1);
  }
  memset(c, 0, sizeof(*c));
  c->h       = NULL;
  c->mock_fd = fd;
  return (0);
}

static int chan_open_uri(chan_t *c, const char *uri)
{
  const char *mock = getenv("IPPUSB_MOCK");

  memset(c, 0, sizeof(*c));
  c->mock_fd = -1;

  if (mock && *mock)
    return (chan_open_mock(c, mock));

  char scheme[32], userpass[64], host[256], resource[512];
  int  port = 0;
  if (httpSeparateURI(HTTP_URI_CODING_ALL, uri, scheme, sizeof(scheme),
                      userpass, sizeof(userpass), host, sizeof(host), &port,
                      resource, sizeof(resource)) < HTTP_URI_STATUS_OK)
  {
    ERR("The printer address is not one this system understands.");
    return (-1);
  }

  /* resource is "/Model" or "/Model?serial=..." */
  char model[256] = "", serial[128] = "";
  const char *r = resource;
  while (*r == '/')
    r++;
  const char *q = strchr(r, '?');
  snprintf(model, sizeof(model), "%.*s", q ? (int)(q - r) : (int)strlen(r), r);
  if (q)
  {
    const char *s = strstr(q, "serial=");
    if (s)
    {
      s += 7;
      const char *e = strchr(s, '&');
      snprintf(serial, sizeof(serial), "%.*s",
               e ? (int)(e - s) : (int)strlen(s), s);
    }
  }

  openctx_t o = { host, model, serial, c, 0 };
  enumerate(open_cb, &o);
  if (!o.opened)
  {
    ERR("The printer is not connected.");
    return (-1);
  }
  return (0);
}


/* ------------------------------------------------------------ mode: list */

static int list_cb(const found_t *f, libusb_device *dev, void *ctx)
{
  char uri[1024], make_model[384], devid[1200];

  (void)dev;
  (void)ctx;
  make_uri(f, uri, sizeof(uri));
  snprintf(make_model, sizeof(make_model), "%s %s", f->mfg, f->mdl);

  /*
   * CMD: is what tells the driver matcher this printer needs no driver. Some
   * printers omit it from their 1284 ID even though they answer IPP, so it is
   * asserted here — reaching this line at all means an IPP-USB interface was
   * found on the device.
   */
  if (f->devid[0])
    snprintf(devid, sizeof(devid), "%s%sCMD:IPP;", f->devid,
             f->devid[strlen(f->devid) - 1] == ';' ? "" : ";");
  else
    snprintf(devid, sizeof(devid), "MFG:%s;MDL:%s;CMD:IPP;", f->mfg, f->mdl);

  cupsBackendReport("direct", uri, make_model, make_model, devid, NULL);
  return (0);
}

static int mode_list(void)
{
  const char *mock = getenv("IPPUSB_MOCK");

  if (mock && *mock)
  {
    /* The harness needs a device to address; give it a stable fake one. */
    cupsBackendReport("direct", "ippusb://Mock/Printer?serial=MOCK1",
                      "Mock Printer", "Mock Printer",
                      "MFG:Mock;MDL:Printer;CMD:IPP;", NULL);
    return (CUPS_BACKEND_OK);
  }
  enumerate(list_cb, NULL);
  return (CUPS_BACKEND_OK);
}


/* ------------------------------------------------------------- mode: ppd */

static int mode_ppd(const char *uri, const char *out)
{
  chan_t c;
  buf_t  pending;
  char   tmpppd[1024];

  if (chan_open_uri(&c, uri) < 0)
    return (1);

  buf_init(&pending);
  ipp_t *attrs = get_printer_attrs(&c, &pending);
  buf_free(&pending);
  if (!attrs)
  {
    ERR("The printer did not describe itself. It may not support driverless "
        "printing.");
    chan_close(&c);
    return (1);
  }

  if (!_ppdCreateFromIPP2(tmpppd, sizeof(tmpppd), attrs, cupsLangDefault()))
  {
    ERR("A driver could not be built from what the printer reported.");
    ippDelete(attrs);
    chan_close(&c);
    return (1);
  }
  ippDelete(attrs);
  chan_close(&c);

  FILE *in = fopen(tmpppd, "rb");
  if (!in)
  {
    ERR("The generated driver could not be read back.");
    return (1);
  }
  /* Write through a temporary and rename, so a reader never sees a half file
   * and a failure part-way leaves the previous one intact. */
  char part[1200];
  snprintf(part, sizeof(part), "%s.part", out);
  FILE *o = fopen(part, "wb");
  if (!o)
  {
    ERR("The driver could not be saved.");
    fclose(in);
    unlink(tmpppd);
    return (1);
  }
  char   blk[8192];
  size_t n;
  int    ok = 1;
  while ((n = fread(blk, 1, sizeof(blk), in)) > 0)
    if (fwrite(blk, 1, n, o) != n)
      ok = 0;
  fclose(in);
  if (fflush(o) || fsync(fileno(o)))
    ok = 0;
  fclose(o);
  unlink(tmpppd);
  if (!ok || rename(part, out))
  {
    unlink(part);
    ERR("The driver could not be saved.");
    return (1);
  }
  return (0);
}


/* ----------------------------------------------------------- mode: attrs */

static int mode_attrs(const char *uri)
{
  chan_t c;
  buf_t  pending;

  if (chan_open_uri(&c, uri) < 0)
    return (1);
  buf_init(&pending);
  ipp_t *attrs = get_printer_attrs(&c, &pending);
  buf_free(&pending);
  chan_close(&c);
  if (!attrs)
    return (1);

  for (ipp_attribute_t *a = ippFirstAttribute(attrs); a;
       a = ippNextAttribute(attrs))
  {
    const char *name = ippGetName(a);
    if (!name)
      continue;
    printf("%s =", name);
    for (int i = 0, n = ippGetCount(a); i < n; i++)
    {
      ipp_tag_t t = ippGetValueTag(a);
      if (t == IPP_TAG_INTEGER || t == IPP_TAG_ENUM)
        printf(" %d", ippGetInteger(a, i));
      else if (t == IPP_TAG_BOOLEAN)
        printf(" %s", ippGetInteger(a, i) ? "true" : "false");
      else
      {
        const char *s = ippGetString(a, i, NULL);
        printf(" %s", s ? s : "");
      }
    }
    putchar('\n');
  }
  ippDelete(attrs);
  return (0);
}


/* ----------------------------------------------------------- mode: print */

/* The formats we can hand a printer, best first. */
static const char * const FORMATS[] = {
  "application/pdf", "image/urf", "image/pwg-raster", "application/postscript",
  "application/vnd.hp-PCL", "image/jpeg", NULL
};

static const char *pick_format(ipp_t *attrs)
{
  const char *final = getenv("FINAL_CONTENT_TYPE");

  /*
   * The queue's PPD already decided what the filter chain produced, so that is
   * the format to declare — anything else and we would be lying to the printer
   * about the bytes we are about to send it. Only when CUPS did not say (a raw
   * queue) do we fall back to the printer's preference order.
   */
  if (final && *final && attr_has(attrs, "document-format-supported", final))
    return (final);
  if (final && *final && !strcasecmp(final, "application/vnd.cups-pdf") &&
      attr_has(attrs, "document-format-supported", "application/pdf"))
    return ("application/pdf");

  for (int i = 0; FORMATS[i]; i++)
    if (attr_has(attrs, "document-format-supported", FORMATS[i]))
      return (FORMATS[i]);
  return ("application/octet-stream");
}

/*
 * Copy a CUPS option across to the IPP request when the printer says it accepts
 * a job attribute of that name. Going by the printer's own
 * job-creation-attributes-supported list is what keeps this general: there is
 * no per-model table to fall out of date, and an option the printer never
 * offered is never sent.
 */
static void map_options(ipp_t *req, ipp_t *attrs, int num_options,
                        cups_option_t *options)
{
  static const struct { const char *cups; const char *ipp; } ALIAS[] = {
    { "media",             "media" },
    { "PageSize",          "media" },
    { "media-source",      "media-source" },
    { "InputSlot",         "media-source" },
    { "media-type",        "media-type" },
    { "MediaType",         "media-type" },
    { "sides",             "sides" },
    { "Duplex",            "sides" },
    { "print-color-mode",  "print-color-mode" },
    { "print-quality",     "print-quality" },
    { "printer-resolution", "printer-resolution" },
    { "orientation-requested", "orientation-requested" },
    { "output-bin",        "output-bin" },
    { "job-sheets",        "job-sheets" },
    { NULL, NULL }
  };

  for (int i = 0; i < num_options; i++)
  {
    const char *name = options[i].name;
    const char *val  = options[i].value;
    const char *ippname = NULL;

    if (!name || !val || !*val)
      continue;

    for (int a = 0; ALIAS[a].cups; a++)
      if (!strcasecmp(name, ALIAS[a].cups))
      {
        ippname = ALIAS[a].ipp;
        break;
      }
    if (!ippname)
      continue;
    if (!attr_has(attrs, "job-creation-attributes-supported", ippname))
      continue;
    if (ippFindAttribute(req, ippname, IPP_TAG_ZERO))
      continue;               /* first setting of a name wins */

    /* Duplex/PageSize carry PPD spellings, not IPP keywords; translate the
     * two that actually differ and pass everything else straight through. */
    if (!strcmp(ippname, "sides"))
    {
      if (!strcasecmp(val, "None") || !strcasecmp(val, "False"))
        val = "one-sided";
      else if (!strcasecmp(val, "DuplexNoTumble"))
        val = "two-sided-long-edge";
      else if (!strcasecmp(val, "DuplexTumble"))
        val = "two-sided-short-edge";
    }

    if (!strcmp(ippname, "print-quality") || !strcmp(ippname, "orientation-requested"))
    {
      int n = atoi(val);
      if (n > 0)
        ippAddInteger(req, IPP_TAG_JOB, IPP_TAG_ENUM, ippname, n);
      continue;
    }
    if (!strcmp(ippname, "printer-resolution"))
    {
      int dpi = atoi(val);
      if (dpi > 0)
        ippAddResolution(req, IPP_TAG_JOB, ippname, IPP_RES_PER_INCH, dpi, dpi);
      continue;
    }
    if (attr_has(attrs, "media-supported", val) || strcmp(ippname, "media"))
      ippAddString(req, IPP_TAG_JOB, IPP_TAG_KEYWORD, ippname, NULL, val);
    else
      DBG("printer does not offer media '%s'; leaving it to the printer", val);
  }
}

/* Report the printer's own state words to CUPS so the UI can show them. */
static void report_state(ipp_t *resp)
{
  ipp_attribute_t *a = ippFindAttribute(resp, "printer-state-reasons",
                                        IPP_TAG_KEYWORD);
  buf_t            s;

  if (!a)
    return;
  buf_init(&s);
  for (int i = 0, n = ippGetCount(a); i < n; i++)
  {
    const char *v = ippGetString(a, i, NULL);
    if (!v || !strcmp(v, "none"))
      continue;
    buf_addf(&s, "%s%s", s.n ? "," : "", v);
  }
  fprintf(stderr, "STATE: %s\n", s.n ? (char *)s.d : "-all");
  fflush(stderr);
  buf_free(&s);
}

/*
 * Watch the job until the printer says it is finished. Without this the backend
 * would exit the moment the printer accepted the bytes, and CUPS would call
 * that a successful print — the very "it said it printed and nothing came out"
 * failure this whole backend exists to end. Polling is also the only place a
 * real reason (out of paper, cover open) can be reported back.
 */
static int wait_for_job(chan_t *c, buf_t *pending, int job_id, const char *user)
{
  time_t deadline = time(NULL) + JOB_POLL_MAX_S;
  int    result   = CUPS_BACKEND_OK;
  int    last     = -1;

  while (time(NULL) < deadline && !g_cancelled)
  {
    ipp_t *req = new_request(IPP_OP_GET_JOB_ATTRIBUTES, c->path, user);
    if (!req)
      break;
    ippAddInteger(req, IPP_TAG_OPERATION, IPP_TAG_INTEGER, "job-id", job_id);

    buf_t msg, reply;
    buf_init(&msg);
    buf_init(&reply);
    if (ipp_serialize(req, &msg) < 0)
    {
      ippDelete(req);
      buf_free(&msg);
      break;
    }
    ippDelete(req);

    int http = ipp_transact(c, pending, msg.d, msg.n, -1, 0, &reply);
    buf_free(&msg);
    if (http != 200)
    {
      buf_free(&reply);
      break;                    /* printer stopped answering; do not guess */
    }

    ipp_t *resp = ipp_parse(&reply);
    buf_free(&reply);
    if (!resp)
      break;

    report_state(resp);
    ipp_attribute_t *st = ippFindAttribute(resp, "job-state", IPP_TAG_ENUM);
    int state = st ? ippGetInteger(st, 0) : 0;
    if (state != last)
    {
      DBG("job-state %d", state);
      last = state;
    }

    if (state >= IPP_JSTATE_CANCELED)
    {
      ipp_attribute_t *r = ippFindAttribute(resp, "job-state-reasons",
                                            IPP_TAG_KEYWORD);
      const char *why = r ? ippGetString(r, 0, NULL) : NULL;
      if (state == IPP_JSTATE_COMPLETED)
      {
        INF("Printed.");
      }
      else
      {
        ERR("The printer did not finish the job%s%s.",
            why ? ": " : "", why ? why : "");
        result = CUPS_BACKEND_FAILED;
      }
      ippDelete(resp);
      return (result);
    }
    ippDelete(resp);
    sleep(2);
  }

  if (g_cancelled)
    return (CUPS_BACKEND_OK);
  /* Falling out of the loop means we lost sight of the job, not that it
   * failed — the bytes were accepted, so do not make CUPS reprint it. */
  DBG("stopped watching the job before the printer reported it finished");
  return (result);
}

static int mode_print(int argc, char *argv[])
{
  const char   *uri  = getenv("DEVICE_URI");
  const char   *user = argv[2];
  const char   *title = argv[3];
  int           copies = atoi(argv[4]);
  int           doc_fd = -1;
  char          spool[1024] = "";
  int           result = CUPS_BACKEND_FAILED;

  if (!uri || !*uri)
    uri = argv[0];
  if (copies < 1)
    copies = 1;

  /*
   * A file argument is the normal case. When CUPS streams on stdin instead the
   * length is unknown, and every byte has to be on disk before the HTTP request
   * can declare a Content-Length — so spool it.
   */
  if (argc > 6)
  {
    doc_fd = open(argv[6], O_RDONLY);
    if (doc_fd < 0)
    {
      ERR("The document could not be opened.");
      return (CUPS_BACKEND_FAILED);
    }
  }
  else
  {
    const char *tmpdir = getenv("TMPDIR");
    snprintf(spool, sizeof(spool), "%s/ippusbXXXXXX",
             tmpdir && *tmpdir ? tmpdir : "/tmp");
    doc_fd = mkstemp(spool);
    if (doc_fd < 0)
    {
      ERR("There was no room to prepare the document for printing.");
      return (CUPS_BACKEND_FAILED);
    }
    char    blk[65536];
    ssize_t n;
    while ((n = read(0, blk, sizeof(blk))) > 0)
      if (write(doc_fd, blk, (size_t)n) != n)
      {
        ERR("There was no room to prepare the document for printing.");
        close(doc_fd);
        unlink(spool);
        return (CUPS_BACKEND_FAILED);
      }
    lseek(doc_fd, 0, SEEK_SET);
  }

  struct stat sb;
  if (fstat(doc_fd, &sb) || sb.st_size <= 0)
  {
    ERR("There is nothing in this document to print.");
    close(doc_fd);
    if (spool[0])
      unlink(spool);
    return (CUPS_BACKEND_FAILED);
  }

  chan_t c;
  buf_t  pending;
  buf_init(&pending);

  /*
   * A printer that is asleep or still enumerating is normal, not an error;
   * CUPS_BACKEND_RETRY tells CUPS to try again rather than stopping the queue.
   */
  if (chan_open_uri(&c, uri) < 0)
  {
    close(doc_fd);
    if (spool[0])
      unlink(spool);
    return (CUPS_BACKEND_RETRY);
  }

  INF("Contacting the printer.");
  ipp_t *attrs = get_printer_attrs(&c, &pending);
  if (!attrs)
  {
    ERR("The printer did not respond to a driverless print request.");
    goto done;
  }
  report_state(attrs);

  const char *format = pick_format(attrs);
  DBG("sending as %s (%lld bytes)", format, (long long)sb.st_size);

  int            num_options = 0;
  cups_option_t *options     = NULL;
  if (argc > 5 && argv[5] && *argv[5])
    num_options = cupsParseOptions(argv[5], 0, &options);

  ipp_t *req = new_request(IPP_OP_PRINT_JOB, c.path, user);
  if (!req)
  {
    cupsFreeOptions(num_options, options);
    ippDelete(attrs);
    goto done;
  }
  ippAddString(req, IPP_TAG_OPERATION, IPP_TAG_MIMETYPE, "document-format",
               NULL, format);
  if (title && *title)
    ippAddString(req, IPP_TAG_OPERATION, IPP_TAG_NAME, "job-name", NULL, title);
  if (copies > 1 && attr_has(attrs, "job-creation-attributes-supported",
                             "copies"))
    ippAddInteger(req, IPP_TAG_JOB, IPP_TAG_INTEGER, "copies", copies);
  map_options(req, attrs, num_options, options);
  cupsFreeOptions(num_options, options);

  buf_t msg, reply;
  buf_init(&msg);
  buf_init(&reply);
  if (ipp_serialize(req, &msg) < 0)
  {
    ippDelete(req);
    ippDelete(attrs);
    buf_free(&msg);
    goto done;
  }
  ippDelete(req);

  INF("Sending the document.");
  int http = ipp_transact(&c, &pending, msg.d, msg.n, doc_fd,
                          (long long)sb.st_size, &reply);
  buf_free(&msg);

  if (http != 200)
  {
    ERR("The printer refused the job (HTTP %d).", http);
    buf_free(&reply);
    ippDelete(attrs);
    goto done;
  }

  ipp_t *resp = ipp_parse(&reply);
  buf_free(&reply);
  if (!resp)
  {
    ERR("The printer's answer could not be read.");
    ippDelete(attrs);
    goto done;
  }

  ipp_status_t st = ippGetStatusCode(resp);
  if (st >= IPP_STATUS_ERROR_BAD_REQUEST)
  {
    ipp_attribute_t *m = ippFindAttribute(resp, "status-message", IPP_TAG_TEXT);
    ERR("The printer would not accept the job%s%s.",
        m ? ": " : "", m ? ippGetString(m, 0, NULL) : "");
    ippDelete(resp);
    ippDelete(attrs);
    goto done;
  }

  ipp_attribute_t *ja = ippFindAttribute(resp, "job-id", IPP_TAG_INTEGER);
  int              job_id = ja ? ippGetInteger(ja, 0) : 0;
  ippDelete(resp);
  ippDelete(attrs);
  INF("The printer accepted the job.");

  if (job_id > 0)
    result = wait_for_job(&c, &pending, job_id, user);
  else
    result = CUPS_BACKEND_OK;

done:
  chan_close(&c);
  buf_free(&pending);
  close(doc_fd);
  if (spool[0])
    unlink(spool);
  return (result);
}


/* -------------------------------------------------------- mode: selftest */

static int expect(int cond, const char *what)
{
  printf("%-58s %s\n", what, cond ? "ok" : "FAIL");
  return (cond ? 0 : 1);
}

static int mode_selftest(void)
{
  int bad = 0;
  char out[128];

  /* buffer */
  buf_t b;
  buf_init(&b);
  buf_add(&b, "hello ", 6);
  buf_addf(&b, "%s%d", "world", 42);
  bad += expect(b.n == 13 && !strcmp((char *)b.d, "hello world42"),
                "buffer append + printf");
  buf_drop(&b, 6);
  bad += expect(!strcmp((char *)b.d, "world42"), "buffer drop keeps the tail");
  buf_drop(&b, 999);
  bad += expect(b.n == 0, "buffer drop past the end empties it");
  buf_free(&b);

  /* IEEE-1284 parsing */
  const char *devid = "MFG:Brother;CMD:PJL,PCL;MDL:MFC-J1355DW;CLS:PRINTER;"
                      "SN:U6412345;";
  devid_field(devid, "MANUFACTURER", "MFG", out, sizeof(out));
  bad += expect(!strcmp(out, "Brother"), "device-id MFG");
  devid_field(devid, "MODEL", "MDL", out, sizeof(out));
  bad += expect(!strcmp(out, "MFC-J1355DW"), "device-id MDL");
  devid_field(devid, "SERIALNUMBER", "SN", out, sizeof(out));
  bad += expect(!strcmp(out, "U6412345"), "device-id SN");
  devid_field(devid, "NOPE", NULL, out, sizeof(out));
  bad += expect(out[0] == '\0', "device-id missing key yields empty");

  /* URI round trip: the shape settings.py and CUPS both have to parse */
  found_t f;
  memset(&f, 0, sizeof(f));
  snprintf(f.mfg, sizeof(f.mfg), "Brother");
  snprintf(f.mdl, sizeof(f.mdl), "MFC-J1355DW");
  snprintf(f.serial, sizeof(f.serial), "U6412345");
  char uri[512];
  make_uri(&f, uri, sizeof(uri));
  bad += expect(!strcmp(uri, "ippusb://Brother/MFC-J1355DW?serial=U6412345"),
                "device URI is built as expected");

  char scheme[32], userpass[64], host[256], resource[512];
  int  port = 0;
  bad += expect(httpSeparateURI(HTTP_URI_CODING_ALL, uri, scheme,
                                sizeof(scheme), userpass, sizeof(userpass),
                                host, sizeof(host), &port, resource,
                                sizeof(resource)) >= HTTP_URI_STATUS_OK &&
                !strcmp(host, "Brother") &&
                !strncmp(resource, "/MFC-J1355DW", 12),
                "device URI parses back to make and model");

  /* IPP encode -> decode round trip through the same code paths the wire uses */
  ipp_t *req = new_request(IPP_OP_PRINT_JOB, "/ipp/print", "someone");
  ippAddString(req, IPP_TAG_OPERATION, IPP_TAG_MIMETYPE, "document-format",
               NULL, "application/pdf");
  buf_t msg;
  buf_init(&msg);
  bad += expect(ipp_serialize(req, &msg) == 0 && msg.n > 20,
                "IPP request serialises");
  ipp_t *back = ipp_parse(&msg);
  bad += expect(back != NULL, "IPP request parses back");
  if (back)
  {
    ipp_attribute_t *a = ippFindAttribute(back, "document-format",
                                          IPP_TAG_MIMETYPE);
    bad += expect(a && !strcmp(ippGetString(a, 0, NULL), "application/pdf"),
                  "IPP attributes survive the round trip");
    ippDelete(back);
  }
  buf_free(&msg);
  ippDelete(req);

  /* format choice honours what the filter chain actually produced */
  ipp_t *attrs = ippNew();
  static const char * const fmts[] = { "image/pwg-raster", "application/pdf" };
  ippAddStrings(attrs, IPP_TAG_PRINTER, IPP_TAG_MIMETYPE,
                "document-format-supported", 2, NULL, fmts);
  setenv("FINAL_CONTENT_TYPE", "image/pwg-raster", 1);
  bad += expect(!strcmp(pick_format(attrs), "image/pwg-raster"),
                "format follows FINAL_CONTENT_TYPE");
  setenv("FINAL_CONTENT_TYPE", "application/vnd.cups-pdf", 1);
  bad += expect(!strcmp(pick_format(attrs), "application/pdf"),
                "cups-pdf is offered to the printer as pdf");
  unsetenv("FINAL_CONTENT_TYPE");
  bad += expect(!strcmp(pick_format(attrs), "application/pdf"),
                "with no hint, the best supported format wins");
  ippDelete(attrs);

  /* an option the printer never advertised is never sent */
  ipp_t *pa = ippNew();
  static const char * const jca[] = { "media", "copies" };
  ippAddStrings(pa, IPP_TAG_PRINTER, IPP_TAG_KEYWORD,
                "job-creation-attributes-supported", 2, NULL, jca);
  static const char * const ms[] = { "na_letter_8.5x11in" };
  ippAddStrings(pa, IPP_TAG_PRINTER, IPP_TAG_KEYWORD, "media-supported", 1,
                NULL, ms);
  cups_option_t *op = NULL;
  int no = cupsParseOptions("PageSize=na_letter_8.5x11in sides=DuplexNoTumble",
                            0, &op);
  ipp_t *jr = new_request(IPP_OP_PRINT_JOB, "/ipp/print", "u");
  map_options(jr, pa, no, op);
  bad += expect(ippFindAttribute(jr, "media", IPP_TAG_KEYWORD) != NULL,
                "PageSize maps to the media the printer offers");
  bad += expect(ippFindAttribute(jr, "sides", IPP_TAG_ZERO) == NULL,
                "an unadvertised attribute is not sent");
  cupsFreeOptions(no, op);
  ippDelete(jr);
  ippDelete(pa);

  printf("\n%s\n", bad ? "SELFTEST FAILED" : "ippusb selftest: OK");
  return (bad ? 1 : 0);
}


/* -------------------------------------------------------------------- main */

int main(int argc, char *argv[])
{
  struct sigaction sa;

  memset(&sa, 0, sizeof(sa));
  sa.sa_handler = on_term;
  sigaction(SIGTERM, &sa, NULL);
  sigaction(SIGINT, &sa, NULL);
  signal(SIGPIPE, SIG_IGN);

  if (argc == 2 && !strcmp(argv[1], "--selftest"))
    return (mode_selftest());
  if (argc == 4 && !strcmp(argv[1], "--ppd"))
    return (mode_ppd(argv[2], argv[3]));
  if (argc == 3 && !strcmp(argv[1], "--attrs"))
    return (mode_attrs(argv[2]));

  /* CUPS backend contract: no arguments means "what printers are there?". */
  if (argc == 1)
    return (mode_list());

  if (argc < 6 || argc > 7)
  {
    fprintf(stderr,
            "Usage: ippusb job-id user title copies options [file]\n"
            "       ippusb --ppd URI OUT.ppd\n"
            "       ippusb --attrs URI\n"
            "       ippusb --selftest\n");
    return (CUPS_BACKEND_FAILED);
  }

  return (mode_print(argc, argv));
}
