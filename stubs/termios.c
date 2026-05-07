/* FUNCTION: tcgetattr */

int __VERIFIER_nondet_int(void);

int tcgetattr(int fd, void *termios_p)
{
  __CPROVER_HIDE:;
  (void)fd;
  (void)termios_p;
  return __VERIFIER_nondet_int() ? 0 : -1;
}

/* FUNCTION: tcsetattr */

int __VERIFIER_nondet_int(void);

int tcsetattr(int fd, int optional_actions, const void *termios_p)
{
  __CPROVER_HIDE:;
  (void)fd;
  (void)optional_actions;
  (void)termios_p;
  return __VERIFIER_nondet_int() ? 0 : -1;
}

/* FUNCTION: isatty */

int __VERIFIER_nondet_int(void);

int isatty(int fd)
{
  __CPROVER_HIDE:;
  (void)fd;
  return __VERIFIER_nondet_int();
}

/* FUNCTION: atexit */

int atexit(void (*function)(void))
{
  __CPROVER_HIDE:;
  (void)function;
  return 0;
}

/* FUNCTION: ioctl */

int __VERIFIER_nondet_int(void);

int ioctl(int fd, unsigned long request, ...)
{
  __CPROVER_HIDE:;
  (void)fd;
  (void)request;
  return __VERIFIER_nondet_int() ? 0 : -1;
}
