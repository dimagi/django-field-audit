from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from field_audit.utils import class_import_helper, get_fqcn

from .models import Flight


class TestClassImportHelper(SimpleTestCase):

    def test_class_import_helper(self):
        cls = class_import_helper(
            "tests.models.Flight",
            "flight_class",
            Flight.__bases__[0],
        )
        self.assertIs(Flight, cls)

    def test_class_import_helper_allows_require_type_tuple(self):
        cls = class_import_helper(
            "tests.models.Flight",
            "flight_class",
            Flight.__bases__,
        )
        self.assertIs(Flight, cls)

    def test_class_import_helper_non_string_raises_valueerror(self):
        with self.assertRaises(ValueError):
            class_import_helper(Flight, "flight_class", Flight.__bases__)

    def test_class_import_helper_invalid_path_raises_improperlyconfigured(self):
        with self.assertRaises(ImportError):
            # ensure this module doesn't actually exist
            import _  # noqa: F401
        with self.assertRaises(ImproperlyConfigured):
            class_import_helper("_.Flight", "flight_class", Flight.__bases__)

    def test_class_import_helper_wrong_required_type_raises_valueerror(self):
        self.assertFalse(issubclass(Flight, str))  # ensure it's actually not
        with self.assertRaises(ValueError):
            class_import_helper("tests.models.Flight", "flight_class", str)

    def test_class_import_helper_require_type_optional(self):
        func = class_import_helper("field_audit.utils.class_import_helper", "f")
        self.assertIs(class_import_helper, func)


class TestGetFqcn(SimpleTestCase):

    def test_get_fqcn(self):
        self.assertEqual(f"{self.__module__}.Out", get_fqcn(Out))

    def test_get_fqcn_nested(self):
        self.assertEqual(f"{self.__module__}.Out.In", get_fqcn(Out.In))


class Out:
    class In:
        pass
