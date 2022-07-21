import sys

from django.db import connection

is_initialized = False


def init_db():
    global is_initialized
    if is_initialized:
        return
    is_initialized = True

    # replace sys.stdout for prompt to delete database
    old_stdout = sys.stdout
    sys.stdout = sys.__stdout__
    try:
        connection.creation.create_test_db(verbosity=0)
    finally:
        sys.stdout = old_stdout


def destroy_db():
    connection.creation.destroy_test_db(verbosity=0)
