/*
 * Highvisor.app launcher stub — a stable TCC identity for the daemon.
 *
 * THE PROBLEM. macOS attributes privacy permissions (Screen Recording, Accessibility) to a
 * "responsible process", not to whatever binary happens to be running. A daemon started from a
 * terminal inherits the TERMINAL's grants, which is why highvisor worked for a year without
 * anyone thinking about it. Start the same interpreter from launchd and the responsible process
 * becomes the job itself — so the grant no longer applies, `CGWindowListCopyWindowInfo` returns
 * every window with a blank title, captures come back empty, and it surfaces three layers away
 * as "ocr failed" or "text 'continue' not on screen". Measured A/B, same binary, same venv:
 * launchd-spawned sees titles as "?", shell-spawned reads them fine.
 *
 * Granting the interpreter itself is possible but bad: the grant is keyed to
 * `.venv/bin/python`, so it evaporates when the venv is rebuilt or Python is upgraded, and it
 * hands Screen Recording to EVERY script that interpreter ever runs.
 *
 * THE FIX. Give the daemon one stable, signed bundle identity to grant. Grant "Highvisor" once
 * and it survives venv rebuilds, Python upgrades and every source edit.
 *
 * WHY THIS FORKS INSTEAD OF exec()ing. The obvious stub is a one-line `execv` to python — and it
 * would defeat the whole point: exec REPLACES the process image, so the running process becomes
 * the interpreter and TCC has an interpreter to attribute to again. Responsibility flows from
 * parent to CHILD (that is the very mechanism that makes a terminal-launched daemon inherit the
 * terminal's grants), so the stub has to stay alive as the parent and let python be its child.
 * It is small on purpose: the Python daemon's own source-change re-exec and its ScreenCaptureKit
 * helper subprocess both stay inside this process tree, and none of them rebuild this binary.
 *
 * That matters because the bundle is ad-hoc signed: rebuilding this file changes the cdhash, and
 * macOS may then treat it as a new app and want the grant again. Keep it boring and it never
 * needs rebuilding.
 *
 * HV_PYTHON and HV_REPO are baked in at build time by tools/make_app.sh.
 */
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef HV_PYTHON
#error "HV_PYTHON must be defined at build time (tools/make_app.sh)"
#endif
#ifndef HV_REPO
#error "HV_REPO must be defined at build time (tools/make_app.sh)"
#endif

static volatile pid_t g_child = 0;

/* Forward the signal launchd (or a human) sends us, so `launchctl bootout` and Ctrl-C stop the
 * daemon rather than orphaning it holding port 48720 — an orphan there looks exactly like a
 * daemon that will not start. */
static void forward(int sig)
{
	if (g_child > 0)
		kill(g_child, sig);
}

int main(int argc, char **argv)
{
	/* The daemon is normally pip-installed into the venv, but keep the repo importable so a
	 * plain checkout works too — same contract as the ~/bin/hv wrapper. */
	setenv("PYTHONPATH", HV_REPO, 1);

	signal(SIGTERM, forward);
	signal(SIGINT, forward);
	signal(SIGHUP, forward);

	g_child = fork();
	if (g_child < 0)
		return 1;

	if (g_child == 0) {
		/* argv is passed through so the app can take the same flags as `hvd`. */
		char *args[64];
		int n = 0;
		args[n++] = (char *)HV_PYTHON;
		args[n++] = (char *)"-m";
		args[n++] = (char *)"highvisor.server";
		for (int i = 1; i < argc && n < 62; i++)
			args[n++] = argv[i];
		args[n] = NULL;
		execv(HV_PYTHON, args);
		_exit(127);            /* only reached if execv failed */
	}

	/* waitpid is interrupted by every signal we forward, so retry on EINTR only — a bare
	 * `while (waitpid(...) < 0)` would spin forever on a real error. */
	int status = 0;
	while (waitpid(g_child, &status, 0) < 0) {
		if (errno != EINTR)
			return 1;
	}
	if (WIFEXITED(status))
		return WEXITSTATUS(status);
	return 1;
}
