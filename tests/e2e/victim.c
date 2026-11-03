/* e2e: leaves a mark when it runs. A transfer damaged on the way must never reach this point. */
#include <stdio.h>

int main(void) {
    FILE *f = fopen("/tmp/qnxrun-victim-ran", "w");
    if (f) fclose(f);
    puts("victim ran");
    return 3;
}
