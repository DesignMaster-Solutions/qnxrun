/* e2e: a parent and a child that ignore SIGTERM and spin; --timeout must kill both on the target. */
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

int main(void) {
    signal(SIGTERM, SIG_IGN);
    signal(SIGINT, SIG_IGN);
    if (fork() == 0) for (;;) {}
    puts("hanging");
    for (;;) {}
}
