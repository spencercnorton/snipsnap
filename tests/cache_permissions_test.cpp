#include "config/cacheutils.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>
#include <sys/stat.h>

namespace {

int permissions(const QString& path)
{
    struct stat metadata{};
    if (::stat(QFile::encodeName(path).constData(), &metadata) != 0) {
        return -1;
    }
    return static_cast<int>(metadata.st_mode & 0777);
}

} // namespace

class CachePermissionsTest : public QObject
{
    Q_OBJECT

public:
    explicit CachePermissionsTest(QString root)
      : m_root(std::move(root))
    {}

private slots:
    void cacheDirectoryIsOwnerOnly()
    {
        const mode_t previousMask = ::umask(0);
        const QString cachePath = getCachePath();
        ::umask(previousMask);

        QVERIFY(cachePath.startsWith(m_root));
        QCOMPARE(permissions(cachePath), 0700);
    }

    void regionCacheIsAtomicPrivateAndRoundTrips()
    {
        const QRect expected(17, 29, 640, 480);
        const mode_t previousMask = ::umask(0);
        setLastRegion(expected);
        ::umask(previousMask);

        const QString regionPath =
          getCachePath() + QStringLiteral("/region.txt");
        QCOMPARE(permissions(regionPath), 0600);
        QCOMPARE(getLastRegion(), expected);
    }

    void existingPermissionsAreRestricted()
    {
        const QString directoryPath = m_root + QStringLiteral("/existing");
        QVERIFY(QDir().mkpath(directoryPath));
        QVERIFY(::chmod(QFile::encodeName(directoryPath).constData(), 0777) ==
                0);
        QVERIFY(ensureOwnerOnlyDirectory(directoryPath));
        QCOMPARE(permissions(directoryPath), 0700);

        const QString filePath = directoryPath + QStringLiteral("/private.dat");
        QFile file(filePath);
        QVERIFY(file.open(QIODevice::WriteOnly));
        QCOMPARE(file.write("screenshot metadata"), qint64(19));
        file.close();
        QVERIFY(::chmod(QFile::encodeName(filePath).constData(), 0666) == 0);
        QVERIFY(ensureOwnerOnlyFile(filePath));
        QCOMPARE(permissions(filePath), 0600);
    }

private:
    QString m_root;
};

int main(int argc, char* argv[])
{
    QTemporaryDir cacheRoot;
    if (!cacheRoot.isValid()) {
        return 1;
    }
    qputenv("XDG_CACHE_HOME", cacheRoot.path().toUtf8());

    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("snipsnap-test"));
    QCoreApplication::setOrganizationName(QStringLiteral("snipsnap"));

    CachePermissionsTest test(cacheRoot.path());
    return QTest::qExec(&test, argc, argv);
}

#include "cache_permissions_test.moc"
