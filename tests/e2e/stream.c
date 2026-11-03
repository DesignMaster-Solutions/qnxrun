/* e2e: one line, then it never ends. Only a streaming transport shows the line before the kill. */
#include <stdio.h>
#include <unistd.h>

int main(void) {
    printf("first line, still running\n"); /* no fflush: a pipe would keep this buffered forever */
    for (;;) pause();
}
