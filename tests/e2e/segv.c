/* e2e: dies by SIGSEGV; the host must see 128 + 11. */
#include <signal.h>

int main(void) {
    raise(SIGSEGV);
    return 0;
}
