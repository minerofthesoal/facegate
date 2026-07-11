# Maintainer: Blaze Industries Research
# Built directly from the checked-out repo tree (no source= tarball) --
# CI stamps pkgver via sed before running makepkg. See
# .github/workflows/build-release.yml.
pkgname=facegate
pkgver=0.1.0
pkgrel=1
pkgdesc="Face unlock (RGB+IR) for Logitech Brio webcams via PAM, Howdy-style"
arch=('any')
url="https://github.com/OWNER/REPO"
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
