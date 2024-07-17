from io import BytesIO

from python3_commons.audit import GeneratedStream, generate_archive


def test_generated_stream(s3_file_objects):
    expected_data = b''
    generator = generate_archive(s3_file_objects, chunk_size=5 * 1024 * 1024)
    archive_stream = GeneratedStream(generator)
    archived_data = archive_stream.read()

    with open('/tmp/test.tar', 'wb') as f:
        f.write(archived_data)

    assert archived_data == expected_data


def test_generated_stream_by_chunks(s3_file_objects):
    expected_data = b''
    generator = generate_archive(s3_file_objects, chunk_size=2)
    archive_stream = GeneratedStream(generator)
    archived_data = BytesIO()

    with open('/tmp/test_chunked.tar', 'wb') as f:
        while chunk := archive_stream.read(2):
            f.write(chunk)
            archived_data.write(chunk)

    assert archived_data.getvalue() == expected_data
