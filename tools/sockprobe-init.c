/*
 * sockprobe-init: PID 1 for the no-internet kernel boot test.
 * Mounts /proc, enumerates /proc/net, probes every historical address
 * family, prints a machine-greppable report on the serial console, and
 * powers off. Statically linked; the initramfs contains only this file.
 *
 * Expected result on the no-internet fork:
 *   the local-only families AF_UNIX (1), AF_NETLINK (16), AF_BLUETOOTH (31)
 *   and AF_ALG (38) are SUPPORTED; every other (internet) family reports
 *   EAFNOSUPPORT -- notably AF_INET (2), AF_INET6 (10) and AF_PACKET (17).
 *
 * On AF_ALG: it is a socket family but NOT a network protocol. It is the
 * userspace interface to the kernel's own crypto engine -- there is no
 * addressing, no peer, and nothing reachable off the machine, exactly like
 * AF_UNIX. It is enabled because BlueZ builds its LE crypto on it:
 * src/shared/crypto.c requests ecb(aes) via "skcipher" and cmac(aes) via
 * "hash", and without it generate_and_write_irk() in bluetoothd's adapter
 * setup returns -1, so no LE adapter ever comes up. Kernel-side Bluetooth
 * SMP does not need it (that uses CRYPTO_LIB_AES/CRYPTO_ECDH directly) --
 * this is purely BlueZ userspace.
 */
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <dirent.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <linux/reboot.h>

struct fam { int af; const char *name; int type; int proto; };

/* BTPROTO_HCI == 1; avoid bluetooth headers so this builds anywhere */
static const struct fam fams[] = {
    {  1, "AF_UNIX     ", SOCK_STREAM, 0 },
    {  2, "AF_INET     ", SOCK_STREAM, 0 },
    {  3, "AF_AX25     ", SOCK_DGRAM,  0 },
    {  4, "AF_IPX      ", SOCK_DGRAM,  0 },
    {  9, "AF_X25      ", SOCK_SEQPACKET, 0 },
    { 10, "AF_INET6    ", SOCK_STREAM, 0 },
    { 15, "AF_KEY      ", SOCK_RAW,    2 },
    /* proto 2 == NETLINK_USERSOCK, the always-registered netlink protocol.
     * We deliberately do NOT probe proto 0 (NETLINK_ROUTE/rtnetlink): the
     * routing netlink is part of the deleted internet stack, so protocol 0
     * is unregistered and returns EPROTONOSUPPORT even though the AF_NETLINK
     * family itself is fully present. */
    { 16, "AF_NETLINK  ", SOCK_RAW,    2 },
    { 17, "AF_PACKET   ", SOCK_RAW,    0 },
    { 21, "AF_RDS      ", SOCK_SEQPACKET, 0 },
    { 29, "AF_CAN      ", SOCK_RAW,    1 },
    { 30, "AF_TIPC     ", SOCK_RDM,    0 },
    { 31, "AF_BLUETOOTH", SOCK_RAW,    1 },
    { 38, "AF_ALG      ", SOCK_SEQPACKET, 0 },
    { 40, "AF_VSOCK    ", SOCK_STREAM, 0 },
};

int main(void)
{
    mkdir("/proc", 0555);
    mkdir("/sys", 0555);
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sysfs", "/sys", "sysfs", 0, NULL);

    printf("\n=== SOCKPROBE-BEGIN ===\n");

    int fd = open("/proc/version", O_RDONLY);
    if (fd >= 0) {
        char buf[256];
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        if (n > 0) { buf[n] = 0; printf("KERNEL: %s", buf); }
        close(fd);
    }

    DIR *d = opendir("/proc/net");
    if (!d) {
        printf("PROC-NET: absent (errno=%d %s)\n", errno, strerror(errno));
    } else {
        printf("PROC-NET:");
        struct dirent *e;
        int n = 0;
        while ((e = readdir(d)) != NULL) {
            if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
                continue;
            printf(" %s", e->d_name);
            n++;
        }
        printf("%s\n", n ? "" : " (empty)");
        closedir(d);
    }

    int bad = 0;
    for (size_t i = 0; i < sizeof(fams) / sizeof(fams[0]); i++) {
        /* The local-only families that MUST be supported in this fork:
         * AF_UNIX (1), AF_NETLINK (16), AF_BLUETOOTH (31) and AF_ALG (38).
         * None of them can carry traffic off the machine. Every other
         * (internet) family MUST report EAFNOSUPPORT. */
        int must_work = (fams[i].af == 1 || fams[i].af == 16 ||
                         fams[i].af == 31 || fams[i].af == 38);
        int s = socket(fams[i].af, fams[i].type, fams[i].proto);
        if (s >= 0) {
            printf("FAM %s -> SUPPORTED\n", fams[i].name);
            if (!must_work)
                bad++;   /* an internet family must NOT exist */
            close(s);
        } else {
            printf("FAM %s -> errno=%d (%s)\n",
                   fams[i].name, errno, strerror(errno));
            if (must_work)
                bad++;   /* a local-IPC family must work */
        }
    }

    printf(bad ? "=== SOCKPROBE-FAIL (%d wrong) ===\n"
               : "=== SOCKPROBE-PASS ===\n", bad);

    sync();
    reboot(LINUX_REBOOT_CMD_POWER_OFF);
    for (;;)
        pause();
}
