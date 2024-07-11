import os
import sys
import tarfile
from io import BytesIO


class FileStream(object):
    def __init__(self):
        self.buffer = BytesIO()
        self.offset = 0

    def write(self, chunk):
        self.buffer.write(chunk)
        self.offset += len(chunk)

    def tell(self):
        return self.offset

    def close(self):
        self.buffer.close()

    def pop(self):
        value = self.buffer.getvalue()
        self.buffer.seek(0)
        self.buffer.truncate()

        return value


def stream_build_tar(in_filename, streaming_fp):
    tar = tarfile.TarFile.open(out_filename, 'w|gz', streaming_fp)

    stat = os.stat(in_filename)

    tar_info = tarfile.TarInfo(in_filename)

    # Note that you can get this information from the storage backend,
    # but it's valid for either to raise a NotImplementedError, so it's
    # important to check.
    #
    # Things like the mode or ownership won't be available.
    tar_info.mtime = stat.st_mtime
    tar_info.size = stat.st_size

    # Note that we don't pass a fileobj, so we don't write any data
    # through addfile. We'll do this ourselves.
    tar.addfile(tar_info)

    yield

    with open(in_filename, 'rb') as in_fp:
        total_size = 0

        while True:
            s = in_fp.read(BLOCK_SIZE)

            if len(s) > 0:
                tar.fileobj.write(s)

                yield

            if len(s) < BLOCK_SIZE:
                blocks, remainder = divmod(tar_info.size, tarfile.BLOCKSIZE)

                if remainder > 0:
                    tar.fileobj.write(tarfile.NUL *
                                      (tarfile.BLOCKSIZE - remainder))

                    yield

                    blocks += 1

                tar.offset += blocks * tarfile.BLOCKSIZE
                break

    tar.close()

    yield


BLOCK_SIZE = 4096


if len(sys.argv) != 3:
    print('Usage: %s in_filename out_filename' % sys.argv[0])
    sys.exit(1)

in_filename = sys.argv[1]
out_filename = sys.argv[2]

streaming_fp = FileStream()

with open(out_filename, 'wb') as out_fp:
    for i in stream_build_tar(in_filename, streaming_fp):
        s = streaming_fp.pop()

        if len(s) > 0:
            print('Writing %d bytes...' % len(s))
            out_fp.write(s)
            out_fp.flush()

print('Wrote tar file to %s' % out_filename)