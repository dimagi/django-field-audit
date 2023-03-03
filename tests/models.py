from django.db.models import (
    CASCADE,
    SET_NULL,
    Model,
    AutoField,
    BooleanField,
    CharField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    IntegerField,
    JSONField,
)

from field_audit import audit_fields
from field_audit.models import AuditingManager


@audit_fields("id", "value")
class SimpleModel(Model):
    id = AutoField(primary_key=True)
    value = CharField(max_length=8, null=True)


@audit_fields("id", "value", audit_special_queryset_writes=True)
class ModelWithAuditingManager(Model):
    id = AutoField(primary_key=True)
    value = CharField(max_length=8, null=True)
    non_audited_field = CharField(max_length=12, null=True)
    objects = AuditingManager()


@audit_fields("id", "save_count")
class ModelWithValueOnSave(Model):
    id = AutoField(primary_key=True)
    value = CharField(max_length=16, null=True)
    save_count = IntegerField(default=0)

    def save(self, *args, **kwargs):
        self.save_count += 1
        super().save(*args, **kwargs)


@audit_fields("name", "title", "flight_hours")
class CrewMember(Model):
    id = AutoField(primary_key=True)
    name = CharField(max_length=256)
    title = CharField(max_length=64)
    flight_hours = DecimalField(max_digits=10, decimal_places=4, default=0.0)


@audit_fields("tail_number", "make_model", "operated_by")
class Aircraft(Model):
    id = AutoField(primary_key=True)
    tail_number = CharField(max_length=32, unique=True)
    make_model = CharField(max_length=64)
    operated_by = CharField(max_length=64)


@audit_fields("icao", "elevation_amsl", "amsl_unit")
class Aerodrome(Model):
    UNITS = {
        "ft": "Feet",
        "m": "Meters",
        "mm": "Millimeters",
    }
    icao = CharField(max_length=4, primary_key=True)
    elevation_amsl = IntegerField()
    amsl_unit = CharField(max_length=2, choices=UNITS.items())


@audit_fields("aircraft", "left_seat", "right_seat", "origin", "destination",
              "departed", "arrived", "diverted")
class Flight(Model):
    id = AutoField(primary_key=True)
    aircraft = ForeignKey(Aircraft, on_delete=CASCADE)
    left_seat = ForeignKey(
        CrewMember,
        on_delete=SET_NULL,
        null=True,
        related_name="left_flights",
    )
    right_seat = ForeignKey(
        CrewMember,
        on_delete=SET_NULL,
        null=True,
        related_name="right_flights",
    )
    origin = ForeignKey(
        Aerodrome,
        on_delete=CASCADE,
        related_name="departures",
    )
    destination = ForeignKey(
        Aerodrome,
        on_delete=CASCADE,
        related_name="arrivals",
    )
    departed = DateTimeField(null=True)
    arrived = DateTimeField(null=True)
    diverted = BooleanField(default=False)


@audit_fields("id")
class PkAuto(Model):
    id = AutoField(primary_key=True)


@audit_fields("id")
class PkJson(Model):
    id = JSONField(primary_key=True)
