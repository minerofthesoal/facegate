
# Maintainer: Ray0rf1re
# Built directly from the checked-out repo tree (no source= tarball) --
# this PKGBUILD is for CI (build-release.yml), which already has the
# tagged commit checked out. The AUR submission uses a *different*
# PKGBUILD (git+https source) that lives only in the aur-facegate repo.
pkgname=facegate
pkgver=0.2.0
pkgrel=1
pkgdesc="Face unlock (RGB+IR) for Logitech webcams via PAM, Howdy-style"
arch=('any')
url="https://github.com/minerofthesoal/facegate"
license=('MIT')
depends=('python' 'python-numpy' 'opencv' 'python-pam' 'v4l-utils')
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
options=('!strip' '!debug')
source=()
sha256sums=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
}
EOF
