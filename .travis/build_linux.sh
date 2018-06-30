#!/usr/bin/env bash

# Builds our app and bundles it into AppImage, along with all of its dependencies.
#
# This script is intended to be ran inside a disposable docker container or chroot, don't run on a
# live system as it does some nasty stuff, like replacing /usr/bin/ld with a MIPS version of it.
#
# Note that AppImage bundles only link-time dependencies, it has no knowlege of the run-time
# dlopen() dependencies, so it won't bundle those.
#
# Also, AppImage is not well-tested aside amd64 and i386 architectures, so there might be some bugs
# related to other architectures.

set -exuo pipefail

# Creates an executable bash script calling some other executable, possibly passing additional args to it.
create_wrapper()
{
    SOURCE=$1
    DEST=$2

    echo "
    #!/bin/sh
    ${SOURCE} \"\$@\"
    " > ${DEST}
    chmod +x ${DEST}
}

build_native()
{
    TRIPLE=$1
    ARCH=$2

    cp /usr/bin/ldd /usr/bin/${TRIPLE}-ldd

    dpkg --add-architecture ${ARCH}
    apt-get update

    apt_install_native "gcc g++"

    build $TRIPLE $ARCH ""
}

# Build using a made-up toolchain that is just a wrapper for the multilib.
# Some architectures on Debian have only multilib support, which is different from using a cross-compiler,
# you basically use your system toolchain but pass some extra compiler or linker flags indicating the arch.
build_with_multilib_toolchain()
{
    TRIPLE=$1
    ARCH=$2
    COMPILER_FLAGS=$3
    LINKER_FLAGS=$4

    create_wrapper "/usr/bin/x86_64-linux-gnu-gcc ${COMPILER_FLAGS}" /usr/bin/${TRIPLE}-gcc
    create_wrapper "/usr/bin/x86_64-linux-gnu-g++ ${COMPILER_FLAGS}" /usr/bin/${TRIPLE}-g++
    create_wrapper "/usr/bin/x86_64-linux-gnu-ar"                    /usr/bin/${TRIPLE}-ar
    create_wrapper "/usr/bin/x86_64-linux-gnu-ld  ${LINKER_FLAGS}"   /usr/bin/${TRIPLE}-ld
    create_wrapper "/usr/bin/x86_64-linux-gnu-objcopy"               /usr/bin/${TRIPLE}-objcopy
    create_wrapper "/usr/bin/x86_64-linux-gnu-readelf"               /usr/bin/${TRIPLE}-readelf
    create_wrapper "/usr/bin/x86_64-linux-gnu-strip"                 /usr/bin/${TRIPLE}-strip
    create_wrapper "/usr/bin/x86_64-linux-gnu-objdump"               /usr/bin/${TRIPLE}-objdump
    cp /usr/bin/ldd /usr/bin/${TRIPLE}-ldd

    dpkg --add-architecture ${ARCH}
    apt-get update

    apt_install_native "gcc-multilib g++-multilib lib32z1-dev"

    build $TRIPLE $ARCH ""
}

# Build using a cross-compilation toolchain.
build_with_cross_toolchain()
{
    TRIPLE=$1
    ARCH=$2
    EMULATOR=$3

    dpkg --add-architecture ${ARCH}
    apt-get update

    apt_install_native "binutils-${TRIPLE} gcc-${TRIPLE} g++-${TRIPLE} qemu-user-static"

    cp /repo/.travis/tools/xldd/xldd.sh /usr/bin/${TRIPLE}-ldd
    chmod +x /usr/bin/${TRIPLE}-ldd
    export CT_XLDD_ROOT="/"

    build $TRIPLE $ARCH $EMULATOR
}

# Just so that we don't have to repeat the apt arguments over and over.
apt_install_native()
{
    apt-get install -y --no-install-recommends ${@}
}

# Prefixes all packages with ":$ARCH" and installs them.
apt_install_cross()
{
    ARCH=$1
    shift

    package_list=""
    for package in "${@}"; do
        package_list="${package_list} ${package}:${ARCH}"
    done
    apt_install_native $package_list
}

build()
{
    TRIPLE=$1
    ARCH=$2
    EMULATOR=$3

    export MAKEFLAGS=j$(nproc)

    echo "
        SET(CMAKE_SYSTEM_NAME Linux)

        SET(CMAKE_C_COMPILER   /usr/bin/${TRIPLE}-gcc)
        SET(CMAKE_CXX_COMPILER /usr/bin/${TRIPLE}-g++)
        SET(CMAKE_AR           /usr/bin/${TRIPLE}-ar CACHE FILEPATH "Archiver")

        SET(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
        SET(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
        SET(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
    " > /usr/local/share/${TRIPLE}.cmake


    # === Build out app ===

    OUR_APP_DEPS_NATIVE=(
        cmake
        make
        qtbase5-dev-tools
        qt5-default
        qttools5-dev-tools
    )

    OUR_APP_DEPS_CROSS=(
        qtbase5-dev
    )

    apt_install_native "${OUR_APP_DEPS_NATIVE[@]}"
    apt_install_cross ${ARCH} "${OUR_APP_DEPS_CROSS[@]}"

    cd /repo
    rm -rf ./build
    mkdir build
    cd build
    cmake -DCMAKE_BUILD_TYPE=RELEASE \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_TOOLCHAIN_FILE=/usr/local/share/${TRIPLE}.cmake \
          ..
    make
    make DESTDIR=output install


    # === Build LinuxDeployQt ===

    LINUX_DEPLOY_QT_DEPS=(
        ca-certificates
        git-core
        qt5-default
        qtbase5-dev
        qtbase5-dev-tools
        g++
        gcc
        make
    )

    apt_install_native "${LINUX_DEPLOY_QT_DEPS[@]}"

    git clone https://github.com/probonopd/linuxdeployqt linuxdeployqt
    cd linuxdeployqt
    git checkout 26ba6229697bcc61990da98889d2ea425f37aca7
    qmake
    make
    make install
    cd ..
    rm -rf ./linuxdeployqt


    # === Build AppImageKit ===

    APP_IMAGE_KIT_DEPS_NATIVE=(
        ca-certificates
        git-core
        make
        automake
        autoconf
        libtool
        make
        gcc
        g++
        desktop-file-utils
        cmake
        patch
        wget
        xxd
        patchelf
    )

    APP_IMAGE_KIT_DEPS_CROSS=(
        libfuse-dev
        liblzma-dev
        libglib2.0-dev
        libssl-dev
        libinotifytools0-dev
        liblz4-dev
        libcairo-dev
        libarchive-dev
    )

    apt_install_native "${APP_IMAGE_KIT_DEPS_NATIVE[@]}"
    apt_install_cross ${ARCH} "${APP_IMAGE_KIT_DEPS_CROSS[@]}"

    # liblzma-dev installs both .a and .so. Having .so breaks AppImageKit building, as it will pick .so thinking it's .a.
    rm /usr/lib/${TRIPLE}/liblzma.so

    git clone --recursive https://github.com/AppImage/AppImageKit AppImageKit
    cd AppImageKit
    git checkout a01f60bb728af07a73644e677d73467985e8dca7
    sed -i "s|<SOURCE_DIR>/configure |<SOURCE_DIR>/configure --host=${TRIPLE} |" cmake/dependencies.cmake
    sed -i "s|gcc|/usr/bin/${TRIPLE}-gcc|" src/build-runtime.sh.in
    sed -i "s|ld |/usr/bin/${TRIPLE}-ld |" src/build-runtime.sh.in
    sed -i "s|objcopy |/usr/bin/${TRIPLE}-objcopy |" src/build-runtime.sh.in
    sed -i "s|readelf |/usr/bin/${TRIPLE}-readelf |" src/build-runtime.sh.in
    sed -i "s|strip|/usr/bin/${TRIPLE}-strip|" src/build-runtime.sh.in
    sed -i "s|objdump |/usr/bin/${TRIPLE}-objdump |" src/build-runtime.sh.in
    mkdir build
    cd build
    export PKG_CONFIG_LIBDIR=/usr/lib/${TRIPLE}/pkgconfig:/usr/share/pkgconfig
    cmake -DCMAKE_BUILD_TYPE=RELEASE \
          -DCMAKE_TOOLCHAIN_FILE=/usr/local/share/${TRIPLE}.cmake \
          -DUSE_SYSTEM_XZ=ON \
          -DUSE_SYSTEM_INOTIFY_TOOLS=ON \
          -DUSE_SYSTEM_LIBARCHIVE=ON \
          -DBUILD_TESTING=OFF \
          ..
    # Always use 1 job. The build breaks with >1 jobs and it's a known issue, apparently.
    make VERBOSE=1 -j1
    make install
    cd ../..
    rm -rf ./AppImageKit


    # === Create AppImage of out app ===

    create_wrapper "/usr/bin/${TRIPLE}-ldd" /usr/bin/ldd
    rm /usr/bin/strip
    cp /usr/bin/${TRIPLE}-strip /usr/bin/strip
    create_wrapper "${EMULATOR} /usr/lib/${TRIPLE}/qt5/bin/qmake" /usr/bin/${TRIPLE}-qmake
    mv /usr/local/bin/appimagetool /usr/local/bin/appimagetool.orig
    create_wrapper "${EMULATOR} /usr/local/bin/appimagetool.orig" /usr/local/bin/appimagetool
    mv /usr/local/lib/appimagekit/mksquashfs /usr/local/lib/appimagekit/mksquashfs.orig
    create_wrapper "${EMULATOR} /usr/local/lib/appimagekit/mksquashfs.orig" /usr/local/lib/appimagekit/mksquashfs

    # appimagetool refuses to run unless ARCH is set to one of known arches, and all ARCH affects
    # is the name of the final AppImage, there is no arch-dependent code, so we just set it to
    # x86_64 for everything, so that our mips, ppc, etc. arches pass.
    ARCH=x86_64 /usr/lib/x86_64-linux-gnu/qt5/bin/linuxdeployqt output/usr/share/applications/my_app.desktop -bundle-non-qt-libs -appimage -qmake=/usr/bin/${TRIPLE}-qmake
    apt_install_native file

    # Make sure arches match the one we build for.
    file output/usr/lib/*
    file output/usr/bin/*
    file *.AppImage

    /usr/bin/${TRIPLE}-readelf -Wh *.AppImage

    ls -lbh *.AppImage
    mv *.AppImage MyApp_${ARCH}.AppImage

    mkdir /tmp/deploy
    mv *.AppImage /tmp/deploy
}

ARCH=$1

case "$ARCH" in
    "amd64")
        build_native x86_64-linux-gnu $ARCH
        ;;
    "arm64")
        build_with_cross_toolchain aarch64-linux-gnu $ARCH /usr/bin/qemu-aarch64-static
        ;;
    "armel")
        build_with_cross_toolchain arm-linux-gnueabi $ARCH /usr/bin/qemu-arm-static
        ;;
    "armhf")
        build_with_cross_toolchain arm-linux-gnueabihf $ARCH /usr/bin/qemu-arm-static
        ;;
    "i386")
        build_with_multilib_toolchain i386-linux-gnu $ARCH "-m32" "-m elf_i386"
        ;;
    "mips")
        build_with_cross_toolchain mips-linux-gnu $ARCH /usr/bin/qemu-mips-static
        ;;
    "mips64el")
        build_with_cross_toolchain mips64el-linux-gnuabi64 $ARCH /usr/bin/qemu-mips64el-static
        ;;
    "mipsel")
        build_with_cross_toolchain mipsel-linux-gnu $ARCH /usr/bin/qemu-mipsel-static
        ;;
    "ppc64el")
        build_with_cross_toolchain powerpc64le-linux-gnu $ARCH /usr/bin/qemu-ppc64le-static
        ;;
    "s390x")
        build_with_cross_toolchain s390x-linux-gnu $ARCH /usr/bin/qemu-s390x-static
        ;;
    *)
        echo "Usage: $0 {amd64|arm64|armel|armhf|i386|mips|mips64el|mipsel|ppc64el|s390x}"
        exit 1
esac
