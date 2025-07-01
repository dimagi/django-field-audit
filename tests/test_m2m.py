from unittest.mock import ANY, patch

from django.test import TestCase

from field_audit.const import BOOTSTRAP_BATCH_SIZE
from field_audit.models import AuditEvent


class TestAuditEventM2M(TestCase):
    def test_manytomany_field_auditing(self):
        """Test ManyToManyField relationships are properly handled."""
        from .models import CrewMember, Certification

        # Create some certifications
        cert1 = Certification.objects.create(
            name='Private Pilot License',
            certification_type='PPL'
        )
        cert2 = Certification.objects.create(
            name='Instrument Rating',
            certification_type='IR'
        )

        # Create a crew member
        crew_member = CrewMember.objects.create(
            name='Test Pilot',
            title='Captain',
            flight_hours=1500.0
        )

        # Add certifications to crew member
        crew_member.certifications.set([cert1, cert2])

        events = AuditEvent.objects.by_model(CrewMember).order_by('event_date')
        self.assertEqual(events.count(), 2)

        # Check the create event
        create_event = events.filter(is_create=True).first()
        self.assertIsNotNone(create_event)

        # Delta should contain field values including ManyToMany field
        delta = create_event.delta
        self.assertIn('name', delta)
        self.assertIn('title', delta)
        self.assertIn('flight_hours', delta)
        self.assertEqual(delta['certifications']['new'], [])

        update_event = events.filter(is_create=False).first()
        self.assertIsNotNone(update_event)
        delta = update_event.delta
        self.assertEqual(
            set(delta['certifications']['add']), {cert1.id, cert2.id}
        )

    def test_manytomany_field_modification_auditing(self):
        """Test that ManyToManyField changes are properly audited."""
        from .models import CrewMember, Certification

        cert1 = Certification.objects.create(
            name='PPL', certification_type='Private'
        )
        cert2 = Certification.objects.create(
            name='IR', certification_type='Instrument'
        )
        cert3 = Certification.objects.create(
            name='CPL', certification_type='Commercial'
        )

        crew_member = CrewMember.objects.create(
            name='Test Pilot',
            title='Captain',
            flight_hours=1500.0
        )
        crew_member.certifications.set([cert1, cert2])

        # Modify certifications (remove cert2, add cert3)
        crew_member.certifications.set([cert1, cert3])

        events = AuditEvent.objects.by_model(CrewMember).order_by('event_date')
        latest_events = events.filter(is_create=False, is_delete=False)
        self.assertEqual(latest_events.count(), 3)
        certification_deltas = [
            event.delta['certifications'] for event in latest_events
        ]
        self.assertEqual(
            [list(delta) for delta in certification_deltas],
            [['add'], ['remove'], ['add']]
        )
        self.assertEqual(
            [set(list(delta.values())[0]) for delta in certification_deltas],
            [{cert1.id, cert2.id}, {cert2.id}, {cert3.id}]
        )

    def test_manytomany_field_clear_auditing(self):
        """Test that clearing ManyToManyField is properly audited."""
        from .models import CrewMember, Certification

        # Create certifications and crew member
        cert1 = Certification.objects.create(
            name='PPL', certification_type='Private'
        )
        cert2 = Certification.objects.create(
            name='IR', certification_type='Instrument'
        )

        crew_member = CrewMember.objects.create(
            name='Test Pilot',
            title='Captain',
            flight_hours=1500.0
        )
        crew_member.certifications.set([cert1, cert2])

        initial_events_count = AuditEvent.objects.by_model(
            CrewMember
        ).count()

        # Clear all certifications
        crew_member.certifications.clear()

        events = AuditEvent.objects.by_model(CrewMember).order_by('event_date')
        self.assertEqual(events.count(), initial_events_count + 1)

        latest_event = events.filter(
            is_create=False, is_delete=False
        ).last()
        delta = latest_event.delta
        self.assertEqual(
            set(delta['certifications']['remove']), {cert1.id, cert2.id}
        )

    def test_manytomany_field_realtime_auditing_with_add_remove(self):
        """Test M2M changes create audit events immediately via signals."""
        from .models import CrewMember, Certification

        # Create certifications and crew member
        cert1 = Certification.objects.create(
            name='PPL', certification_type='Private'
        )
        cert2 = Certification.objects.create(
            name='IR', certification_type='Instrument'
        )

        crew_member = CrewMember.objects.create(
            name='Test Pilot',
            title='Captain',
            flight_hours=1500.0
        )

        initial_events_count = AuditEvent.objects.by_model(
            CrewMember
        ).count()
        self.assertEqual(initial_events_count, 1)

        # Test direct add() - should create audit event immediately
        crew_member.certifications.add(cert1, cert2)

        # Check audit event was created immediately (without save())
        events = AuditEvent.objects.by_model(CrewMember).order_by(
            'event_date'
        )

        new_events = list(events[initial_events_count:])
        self.assertEqual(
            len(new_events), 1, "M2M add() should create audit event"
        )
        latest_event = new_events[-1]
        self.assertIn('certifications', latest_event.delta)
        self.assertEqual(
            set(latest_event.delta['certifications']['add']),
            {cert1.id, cert2.id}
        )

        # Test direct remove() - should create audit event immediately
        current_events_count = events.count()
        crew_member.certifications.remove(cert1)

        events = AuditEvent.objects.by_model(CrewMember).order_by(
            'event_date'
        )
        self.assertEqual(
            events.count(), current_events_count + 1,
            "M2M remove() should create audit event"
        )

        # Check the remove event
        latest_event = events.last()
        self.assertIn('certifications', latest_event.delta)
        self.assertEqual(
            set(latest_event.delta['certifications']['remove']), {cert1.id}
        )


class TestAuditEventBootstrappingM2M(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from tests.models import Certification, CrewMember
        cls.cert1 = Certification.objects.create(
            name='PPL', certification_type='Private'
        )
        cls.cert2 = Certification.objects.create(
            name='IR', certification_type='Instrument'
        )

        crew_member = CrewMember.objects.create(
            name='Test Pilot',
            title='Captain',
            flight_hours=1500.0
        )
        crew_member.certifications.set([cls.cert1, cls.cert2])

    def test_bootstrap_existing_model_records_m2m(self):
        from tests.models import CrewMember
        self.assertEqual([], list(AuditEvent.objects.filter(is_bootstrap=True)))
        with patch.object(AuditEvent.objects, "bulk_create",
                          side_effect=AuditEvent.objects.bulk_create) as mock:
            created_count = AuditEvent.bootstrap_existing_model_records(
                CrewMember,
                ['certifications'],
            )
            mock.assert_called_once_with(ANY, batch_size=BOOTSTRAP_BATCH_SIZE)
        bootstrap_events = AuditEvent.objects.filter(is_bootstrap=True)
        self.assertEqual(len(bootstrap_events), created_count)
        event = bootstrap_events[0]
        self.assertEqual(
            set(event.delta['certifications']['new']),
            {self.cert1.id, self.cert2.id}
        )
