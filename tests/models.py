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
)

from field_audit import audit_fields


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
