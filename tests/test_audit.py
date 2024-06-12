from python3_commons.audit import GeneratedStream, generate_archive
from python3_commons.serializers import msgpack


def test_generated_stream(s3_file_objects):
    expected_data = b''
    generator = generate_archive(s3_file_objects, chunk_size=5 * 1024 * 1024)
    archive_stream = GeneratedStream(generator)
    archived_data = archive_stream.read()

    assert archived_data == expected_data
