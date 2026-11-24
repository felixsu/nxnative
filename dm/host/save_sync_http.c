// The cloud saves' http(s) transport (save_sync.h, front_save_sync_http): DM_SAVE_SYNC naming a save store, such as a game library's /api/saves, instead of a folder.
// GET <url> brings the cloud's save.dmsync (404: there is none), PUT <url> sends this device's. libcurl from the macOS SDK; linked into the host (Makefile) and the Mac
// launcher (dm/mac/pack_app.py) beside save_sync.c. The callers bound the time a fetch may take (fetch_timed); a connection that does not open in 5 s, or that stalls for
// 30 s, fails here too, so a thread left behind by a fetch nobody waits for still ends.
#include "save_sync.h"
#include <curl/curl.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define HLOG(...) do { fprintf(stderr, "[dm] save sync: " __VA_ARGS__); fputc('\n', stderr); } while (0)

static void global_init(void) { curl_global_init(CURL_GLOBAL_DEFAULT); }

static size_t to_file(char *p, size_t size, size_t n, void *f) { return fwrite(p, size, n, f); }
static size_t discard(char *p, size_t size, size_t n, void *u) { (void)p; (void)u; return size * n; }

static CURL *easy(const char *url) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, global_init);
    CURL *c = curl_easy_init(); if (!c) return NULL;
    curl_easy_setopt(c, CURLOPT_URL, url);
    curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);            // called from the sender's and the fetch's threads
    curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT_MS, 5000L);
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_LIMIT, 1L);     // a transfer that stalls for 30 s fails
    curl_easy_setopt(c, CURLOPT_LOW_SPEED_TIME, 30L);
    curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(c, CURLOPT_USERAGENT, "nxnative-save-sync");
    return c;
}

static int fetch(const char *url, const char *local) {
    FILE *f = fopen(local, "wb"); if (!f) return -1;
    CURL *c = easy(url); if (!c) { fclose(f); unlink(local); return -1; }
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, to_file);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, f);
    long code = 0; CURLcode rc = curl_easy_perform(c);
    if (rc == CURLE_OK) curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(c);
    int closed = fclose(f);
    int r = rc != CURLE_OK || closed ? -1 : code == 200 ? 1 : code == 404 ? 0 : -1;
    if (rc != CURLE_OK) HLOG("reading %s failed: %s", url, curl_easy_strerror(rc));
    else if (r < 0) HLOG("reading %s: HTTP %ld", url, code);
    if (r != 1) unlink(local);
    if (r < 0) errno = EIO;
    return r;
}

static int store(const char *local, const char *url) {
    FILE *f = fopen(local, "rb"); if (!f) return 0;
    struct stat st; if (fstat(fileno(f), &st)) { fclose(f); return 0; }
    CURL *c = easy(url); if (!c) { fclose(f); return 0; }
    struct curl_slist *h = curl_slist_append(NULL, "Content-Type: application/octet-stream");
    h = curl_slist_append(h, "Expect:");   // send the body at once, without waiting for a 100 Continue
    char name[256] = "", line[300];   // the Mac's name for the store's list of saves (the file itself only names the model, as Mac16,1)
    if (!gethostname(name, sizeof name - 1)) {
        char *dot = strstr(name, ".local"); if (dot && !dot[6]) *dot = 0;
        snprintf(line, sizeof line, "X-Save-Device: %s", name); h = curl_slist_append(h, line);
    }
    curl_easy_setopt(c, CURLOPT_UPLOAD, 1L);   // PUT
    curl_easy_setopt(c, CURLOPT_READDATA, f);
    curl_easy_setopt(c, CURLOPT_INFILESIZE_LARGE, (curl_off_t)st.st_size);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, discard);
    long code = 0; CURLcode rc = curl_easy_perform(c);
    if (rc == CURLE_OK) curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
    curl_easy_cleanup(c); curl_slist_free_all(h); fclose(f);
    if (rc != CURLE_OK) HLOG("sending to %s failed: %s", url, curl_easy_strerror(rc));
    else if (code < 200 || code > 299) HLOG("sending to %s: HTTP %ld", url, code);
    int ok = rc == CURLE_OK && code >= 200 && code <= 299;
    if (!ok) errno = EIO;
    return ok;
}

int front_save_sync_http(int put, const char *url, const char *local) { return put ? store(local, url) : fetch(url, local); }
