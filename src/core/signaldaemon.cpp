#include "signaldaemon.h"

#include <QDebug>
#include <QSocketNotifier>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {

bool configureDescriptor(int fd)
{
    const int statusFlags = ::fcntl(fd, F_GETFL);
    if (statusFlags == -1 ||
        ::fcntl(fd, F_SETFL, statusFlags | O_NONBLOCK) == -1) {
        return false;
    }

    const int descriptorFlags = ::fcntl(fd, F_GETFD);
    return descriptorFlags != -1 &&
           ::fcntl(fd, F_SETFD, descriptorFlags | FD_CLOEXEC) != -1;
}

void closeSocketPair(int (&fds)[2])
{
    for (int& fd : fds) {
        if (fd >= 0) {
            ::close(fd);
            fd = -1;
        }
    }
}

bool createSocketPair(int (&fds)[2])
{
    if (::socketpair(AF_UNIX, SOCK_STREAM, 0, fds) == -1) {
        return false;
    }
    if (!configureDescriptor(fds[0]) || !configureDescriptor(fds[1])) {
        closeSocketPair(fds);
        return false;
    }
    return true;
}

void notifySocket(int fd) noexcept
{
    const int savedErrno = errno;
    if (fd >= 0) {
        const char message = 1;
        ssize_t result;
        do {
            result = ::write(fd, &message, sizeof(message));
        } while (result == -1 && errno == EINTR);
        // EAGAIN means a notification is already queued. Dropping this
        // duplicate is safer than ever blocking inside a signal handler.
        (void)result;
    }
    errno = savedErrno;
}

void drainSocket(int fd)
{
    char buffer[64];
    for (;;) {
        const ssize_t result = ::read(fd, buffer, sizeof(buffer));
        if (result > 0) {
            continue;
        }
        if (result == -1 && errno == EINTR) {
            continue;
        }
        if (result == -1 && errno != EAGAIN && errno != EWOULDBLOCK) {
            qWarning() << "Unable to read Unix signal notification:" << errno;
        }
        return;
    }
}

} // namespace

int SignalDaemon::sigintFd[2] = { -1, -1 };
int SignalDaemon::sigtermFd[2] = { -1, -1 };

SignalDaemon::SignalDaemon(QObject* parent)
  : QObject(parent)
{
    if (sigintFd[0] < 0 && !createSocketPair(sigintFd)) {
        qFatal("Couldn't create INT socketpair");
    }

    if (sigtermFd[0] < 0 && !createSocketPair(sigtermFd)) {
        closeSocketPair(sigintFd);
        qFatal("Couldn't create TERM socketpair");
    }
    snInt = new QSocketNotifier(sigintFd[1], QSocketNotifier::Read, this);
    connect(
      snInt,
      &QSocketNotifier::activated,
      this,
      [this](QSocketDescriptor, QSocketNotifier::Type) { handleSigInt(); });
    snTerm = new QSocketNotifier(sigtermFd[1], QSocketNotifier::Read, this);
    connect(
      snTerm,
      &QSocketNotifier::activated,
      this,
      [this](QSocketDescriptor, QSocketNotifier::Type) { handleSigTerm(); });
}

SignalDaemon::~SignalDaemon()
{
    snInt->setEnabled(false);
    snTerm->setEnabled(false);
    // Keep the four descriptors alive until process exit. Closing them here
    // could race with an already-running async handler and let it write to a
    // descriptor that the process has reused for unrelated data.
}

void SignalDaemon::intSignalHandler(int)
{
    notifySocket(sigintFd[0]);
}

void SignalDaemon::termSignalHandler(int)
{
    notifySocket(sigtermFd[0]);
}

void SignalDaemon::handleSigTerm()
{
    snTerm->setEnabled(false);
    drainSocket(sigtermFd[1]);
    emit signalReceived(SIGTERM);
    snTerm->setEnabled(true);
}

void SignalDaemon::handleSigInt()
{
    snInt->setEnabled(false);
    drainSocket(sigintFd[1]);
    emit signalReceived(SIGINT);
    snInt->setEnabled(true);
}
