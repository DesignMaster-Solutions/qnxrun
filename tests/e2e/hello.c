/* e2e: prints its arguments, then exits with the code given as the first one. */
#include <stdio.h>
#include <stdlib.h>
#include <sys/utsname.h>

int main(int argc, char **argv) {
    struct utsname u;
    if (uname(&u) == 0) printf("hello from %s %s %s\n", u.sysname, u.release, u.machine);
    for (int i = 1; i < argc; i++) printf("arg[%d]=[%s]\n", i, argv[i]);
    return argc > 1 ? atoi(argv[1]) : 0;
}
