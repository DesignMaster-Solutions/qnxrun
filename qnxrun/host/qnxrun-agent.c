/* qnxrun-agent: target side of the qnxrun serial protocol. Baked into the image with qcc.
 *
 * Started from the console shell as `qnxrun-agent <nonce>`. It switches the tty to raw and speaks lines:
 *   host -> agent   P <size> <sha256> <path>   then base64 lines; each is acked, the file becomes
 *                                              executable only if the whole content hash matches
 *                   R <timeout_ms> <argc>      then argc base64 lines: run argv, 0 = no timeout
 *                   U <path>                   unlink
 *                   Q                          leave; 0x03 at any moment kills the child and leaves
 *   agent -> host   \n@<nonce> <kind> <text>\n H hello, A ack, V ok|bad, O/E base64 stdout/stderr,
 *                                              X <exit|sig|timeout|abort|exec> <value> <sha256>
 * Everything a child writes is base64 inside a frame, so its output cannot forge the result. The X hash
 * covers all output plus the result itself, so a damaged exit code is detected on the host.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define LINE_MAX_LEN 4096
#define OUT_CHUNK 768
#define WORK_DIR "/tmp/qnxrun"

static const char *nonce;
static struct termios saved_tty;
static int have_tty;

/* ---------- SHA-256 (FIPS 180-4) ---------- */
typedef struct { uint32_t h[8]; uint64_t len; uint8_t buf[64]; size_t n; } sha_t;
static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha_block(sha_t *s, const uint8_t *p) {
    uint32_t w[64], v[8];
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)p[i * 4] << 24 | (uint32_t)p[i * 4 + 1] << 16 | (uint32_t)p[i * 4 + 2] << 8 | p[i * 4 + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t a = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t b = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + a + w[i - 7] + b;
    }
    memcpy(v, s->h, sizeof v);
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = v[7] + (ROR(v[4], 6) ^ ROR(v[4], 11) ^ ROR(v[4], 25)) + ((v[4] & v[5]) ^ (~v[4] & v[6])) + K[i] + w[i];
        uint32_t t2 = (ROR(v[0], 2) ^ ROR(v[0], 13) ^ ROR(v[0], 22)) + ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof v[0]);
        v[4] += t1;
        v[0] = t1 + t2;
    }
    for (int i = 0; i < 8; i++) s->h[i] += v[i];
}

static void sha_init(sha_t *s) {
    static const uint32_t iv[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(s->h, iv, sizeof iv);
    s->len = 0;
    s->n = 0;
}

static void sha_update(sha_t *s, const void *data, size_t len) {
    const uint8_t *p = data;
    s->len += len;
    while (len) {
        size_t take = 64 - s->n < len ? 64 - s->n : len;
        memcpy(s->buf + s->n, p, take);
        s->n += take; p += take; len -= take;
        if (s->n == 64) { sha_block(s, s->buf); s->n = 0; }
    }
}

static void sha_hex(sha_t *s, char out[65]) {
    uint64_t bits = s->len * 8;
    uint8_t pad = 0x80, zero = 0, tail[8];
    sha_update(s, &pad, 1);
    while (s->n != 56) sha_update(s, &zero, 1);
    for (int i = 0; i < 8; i++) tail[i] = (uint8_t)(bits >> (56 - i * 8));
    sha_update(s, tail, 8);
    for (int i = 0; i < 8; i++) sprintf(out + i * 8, "%08x", s->h[i]);
}

/* ---------- base64 ---------- */
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t b64_encode(const uint8_t *in, size_t n, char *out) {
    size_t o = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16 | (i + 1 < n ? (uint32_t)in[i + 1] << 8 : 0) | (i + 2 < n ? in[i + 2] : 0);
        out[o++] = B64[v >> 18 & 63];
        out[o++] = B64[v >> 12 & 63];
        out[o++] = i + 1 < n ? B64[v >> 6 & 63] : '=';
        out[o++] = i + 2 < n ? B64[v & 63] : '=';
    }
    out[o] = 0;
    return o;
}

/* Strict decoder: any byte outside the alphabet is a refusal, never skipped. */
static long b64_decode(const char *in, uint8_t *out) {
    size_t len = strlen(in), o = 0;
    if (len % 4) return -1;
    for (size_t i = 0; i < len; i += 4) {
        uint32_t v = 0;
        int pad = 0;
        for (int j = 0; j < 4; j++) {
            const char *c = in[i + j] && in[i + j] != '=' ? strchr(B64, in[i + j]) : NULL;
            if (in[i + j] == '=') {
                if (i + 4 != len || j < 2) return -1;
                pad++;
            } else if (!c || pad) {
                return -1;
            }
            v = v << 6 | (c ? (uint32_t)(c - B64) : 0);
        }
        out[o++] = (uint8_t)(v >> 16);
        if (pad < 2) out[o++] = (uint8_t)(v >> 8);
        if (pad < 1) out[o++] = (uint8_t)v;
    }
    return (long)o;
}

/* ---------- console I/O ---------- */
static void write_all(int fd, const char *p, size_t n) {
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w < 0) {
            if (errno == EINTR || errno == EAGAIN) continue;
            _exit(3);
        }
        p += w; n -= (size_t)w;
    }
}

static void frame(char kind, const char *fmt, ...) {
    char text[LINE_MAX_LEN], line[LINE_MAX_LEN + 64];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    int n = snprintf(line, sizeof line, "\n@%s %c %s\n", nonce, kind, text);
    write_all(STDOUT_FILENO, line, (size_t)n);
}

static void leave(int rc) {
    if (have_tty) tcsetattr(STDIN_FILENO, TCSANOW, &saved_tty);
    _exit(rc);
}

/* One line from the host; 0x03 anywhere leaves immediately (a new host session is taking over). */
static void read_line(char *buf, size_t cap) {
    size_t n = 0;
    for (;;) {
        char c;
        ssize_t r = read(STDIN_FILENO, &c, 1);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0 || c == 3) leave(130);
        if (c == '\n') { buf[n] = 0; return; }
        if (n + 1 >= cap) { frame('!', "line too long"); leave(4); }
        buf[n++] = c;
    }
}

/* ---------- commands ---------- */
static void cmd_put(char *args) {
    static char line[LINE_MAX_LEN];
    static uint8_t data[LINE_MAX_LEN];
    char want[65], got[65], path[1024], part[1100];
    unsigned long long size, seen = 0;
    if (sscanf(args, "%llu %64s %1023s", &size, want, path) != 3) { frame('!', "bad P"); return; }
    snprintf(part, sizeof part, "%s.part", path);
    int fd = open(part, O_CREAT | O_TRUNC | O_WRONLY, 0600);
    if (fd < 0) { frame('V', "bad open errno=%d", errno); return; }
    sha_t sha;
    sha_init(&sha);
    while (seen < size) {
        read_line(line, sizeof line);
        long n = b64_decode(line, data);
        if (n <= 0 || seen + (unsigned long long)n > size) {
            close(fd); unlink(part);
            frame('V', "bad encoding");
            return;
        }
        write_all(fd, (char *)data, (size_t)n);
        sha_update(&sha, data, (size_t)n);
        seen += (unsigned long long)n;
        if (seen < size) frame('A', "%llu", seen);
    }
    close(fd);
    sha_hex(&sha, got);
    if (strcmp(got, want) != 0) {
        unlink(part);
        frame('V', "bad hash %s", got);
        return;
    }
    if (chmod(part, 0755) != 0 || rename(part, path) != 0) {
        unlink(part);
        frame('V', "bad install errno=%d", errno);
        return;
    }
    frame('V', "ok");
}

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void set_raw(int fd) {
    struct termios t;
    if (tcgetattr(fd, &t) != 0) return;
    t.c_iflag = 0;
    t.c_oflag = 0;
    t.c_lflag = 0;
    t.c_cflag = (t.c_cflag & ~(tcflag_t)(CSIZE | PARENB)) | CS8;
    t.c_cc[VMIN] = 1;
    t.c_cc[VTIME] = 0;
    tcsetattr(fd, TCSANOW, &t);
}

/* Returns 0 at EOF/no data, >0 when it forwarded output. */
static int pump(int fd, char kind, sha_t *sha) {
    uint8_t data[OUT_CHUNK];
    char text[OUT_CHUNK * 2];
    ssize_t r = read(fd, data, sizeof data);
    if (r <= 0) return 0;
    sha_update(sha, &kind, 1);
    sha_update(sha, data, (size_t)r);
    b64_encode(data, (size_t)r, text);
    frame(kind, "%s", text);
    return 1;
}

static void cmd_run(char *args) {
    static char line[LINE_MAX_LEN];
    long long timeout_ms = 0;
    int argc = 0;
    if (sscanf(args, "%lld %d", &timeout_ms, &argc) != 2 || argc < 1 || argc > 256) { frame('!', "bad R"); return; }
    char **argv = calloc((size_t)argc + 1, sizeof *argv);
    for (int i = 0; i < argc; i++) {
        read_line(line, sizeof line);
        argv[i] = calloc(1, strlen(line) + 1);
        long n = b64_decode(line, (uint8_t *)argv[i]);
        if (n < 0 || strlen(argv[i]) != (size_t)n) { frame('!', "bad argument"); leave(4); }
    }
    sha_t sha;
    sha_init(&sha);
    char result[64], hex[65];
    /* A pty, not a pipe: the child's stdio stays line buffered, so output really streams. */
    int master = posix_openpt(O_RDWR | O_NOCTTY), slave = -1, errp[2] = {-1, -1}, execp[2] = {-1, -1};
    if (master >= 0 && grantpt(master) == 0 && unlockpt(master) == 0) slave = open(ptsname(master), O_RDWR | O_NOCTTY);
    if (slave < 0 || pipe(errp) != 0 || pipe(execp) != 0) {
        snprintf(result, sizeof result, "exec %d", errno);
        goto done;
    }
    set_raw(slave);
    fcntl(execp[1], F_SETFD, FD_CLOEXEC);
    pid_t pid = fork();
    if (pid < 0) {
        snprintf(result, sizeof result, "exec %d", errno);
        goto done;
    }
    if (pid == 0) {
        setsid();
        int devnull = open("/dev/null", O_RDONLY);
        dup2(devnull, STDIN_FILENO);
        dup2(slave, STDOUT_FILENO);
        dup2(errp[1], STDERR_FILENO);
        close(master); close(slave); close(errp[0]); close(errp[1]); close(execp[0]); close(devnull);
        if (chdir(WORK_DIR) != 0) { /* the binary is absolute; cwd is best effort */ }
        /* exec hands a file without a known header to sh (QNX does). Only ELF or #! may run; anything else is ENOEXEC. */
        if (strchr(argv[0], '/')) {
            char head[4] = {0};
            int f = open(argv[0], O_RDONLY);
            int known = f >= 0 && read(f, head, 4) >= 2 && (memcmp(head, "\x7f" "ELF", 4) == 0 || memcmp(head, "#!", 2) == 0);
            if (f >= 0) close(f);
            if (known) execv(argv[0], argv);
            else if (f >= 0) errno = ENOEXEC;
        } else {
            execvp(argv[0], argv);
        }
        int e = errno;
        write_all(execp[1], (char *)&e, sizeof e);
        _exit(127);
    }
    close(errp[1]); close(execp[1]);
    int child_errno = 0;
    ssize_t got = read(execp[0], &child_errno, sizeof child_errno);
    close(execp[0]);
    fcntl(master, F_SETFL, O_NONBLOCK);
    fcntl(errp[0], F_SETFL, O_NONBLOCK);
    long long deadline = timeout_ms > 0 ? now_ms() + timeout_ms : 0;
    int status = 0, ended = 0;
    result[0] = 0;
    while (!ended) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(STDIN_FILENO, &fds);
        FD_SET(master, &fds);
        if (errp[0] >= 0) FD_SET(errp[0], &fds);
        struct timeval tv = {0, 50000};
        int top = master > errp[0] ? master : errp[0];
        if (select(top + 1, &fds, NULL, NULL, &tv) > 0) {
            if (FD_ISSET(master, &fds)) pump(master, 'O', &sha);
            if (errp[0] >= 0 && FD_ISSET(errp[0], &fds) && !pump(errp[0], 'E', &sha)) {
                close(errp[0]); /* select said readable and there was nothing: the child closed stderr */
                errp[0] = -1;
            }
            if (FD_ISSET(STDIN_FILENO, &fds)) {
                char c;
                if (read(STDIN_FILENO, &c, 1) == 1 && c == 3) {
                    kill(-pid, SIGKILL);
                    kill(pid, SIGKILL);
                    waitpid(pid, &status, 0);
                    leave(130);
                }
            }
        }
        if (waitpid(pid, &status, WNOHANG) == pid) {
            ended = 1;
        } else if (deadline && now_ms() >= deadline) {
            kill(-pid, SIGKILL);
            kill(pid, SIGKILL);
            waitpid(pid, &status, 0);
            snprintf(result, sizeof result, "timeout 0");
            ended = 1;
        }
    }
    kill(-pid, SIGKILL); /* nothing the run spawned outlives it */
    while (pump(master, 'O', &sha)) {}
    while (errp[0] >= 0 && pump(errp[0], 'E', &sha)) {}
    if (result[0]) { /* timeout already set */ }
    else if (got == (ssize_t)sizeof child_errno) snprintf(result, sizeof result, "exec %d", child_errno);
    else if (WIFEXITED(status)) snprintf(result, sizeof result, "exit %d", WEXITSTATUS(status));
    else snprintf(result, sizeof result, "sig %d", WTERMSIG(status));
done:
    if (master >= 0) close(master);
    if (slave >= 0) close(slave);
    if (errp[0] >= 0) close(errp[0]);
    for (int i = 0; i < argc; i++) free(argv[i]);
    free(argv);
    sha_update(&sha, result, strlen(result));
    sha_hex(&sha, hex);
    frame('X', "%s %s", result, hex);
}

int main(int argc, char **argv) {
    static char line[LINE_MAX_LEN];
    if (argc != 2) {
        fprintf(stderr, "usage: qnxrun-agent <nonce>\n");
        return 2;
    }
    nonce = argv[1];
    mkdir(WORK_DIR, 0755);
    if (tcgetattr(STDIN_FILENO, &saved_tty) == 0) {
        have_tty = 1;
        set_raw(STDIN_FILENO);
    }
    frame('H', "1");
    for (;;) {
        read_line(line, sizeof line);
        switch (line[0]) {
        case 'P': cmd_put(line + 1); break;
        case 'R': cmd_run(line + 1); break;
        case 'U': frame('A', "%d", unlink(line + 2) == 0 ? 0 : errno); break;
        case 'Q': leave(0); break;
        default: frame('!', "unknown command"); break;
        }
    }
}
