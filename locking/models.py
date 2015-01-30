from __future__ import absolute_import, unicode_literals, division

from django.conf import settings
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from .settings import EXPIRATION_SECONDS


__all__ = ('Lock', )


class QueryMixin(object):
    def unexpired(self):
        return self.filter(date_expires__gte=timezone.now())

    # Django < 1.6
    if not hasattr(models.query.QuerySet, 'first'):
        def first(self):
            try:
                return self.all()[0]
            except IndexError:
                return None


class LockingQuerySet(QueryMixin, models.query.QuerySet):
    pass


class LockingManager(QueryMixin, models.Manager):

    def delete_expired(self):
        """Delete all expired locks from the database"""
        self.filter(date_expires__lt=timezone.now()).delete()

    def lock_for_user(self, content_type, object_id, user):
        """
        Try to create a lock for a user for a given content_type / object id.

        If a lock does not exist (or has expired) the current user gains a lock
        on this object. If another user already has a valid lock on this object,
        then Lock.ObjectLockedError is raised.

        """

        try:
            lock = self.get(content_type=content_type, object_id=object_id)
        except Lock.DoesNotExist:
            lock = Lock(content_type=content_type, object_id=object_id, locked_by=user)
        else:
            if lock.has_expired:
                lock.locked_by = user
            elif lock.locked_by.id != user.id:
                raise Lock.ObjectLockedError('This object is already locked by another user')
        lock.save()
        return lock

    def lock_object_for_user(self, obj, user):
        """Calls `lock_for_user` on a given object and user."""
        ct_type = ContentType.objects.get_for_model(obj)
        return self.lock_for_user(content_type=ct_type, object_id=obj.pk, user=user)

    def get_queryset(self):
        return LockingQuerySet(self.model)

    def get_query_set(self):
        """Compatibility with Django < 1.6"""
        return self.get_queryset()


class Lock(models.Model):
    id = models.CharField(max_length=15, primary_key=True)
    locked_by = models.ForeignKey(getattr(settings, 'AUTH_USER_MODEL', 'auth.User'))
    date_expires = models.DateTimeField()
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = generic.GenericForeignKey('content_type', 'object_id')

    objects = LockingManager()

    class Meta:
        unique_together = ('content_type', 'object_id', )
        permissions = (("can_unlock", "Can remove other user's locks"), )

    class ObjectLockedError(Exception):
        pass

    def save(self, *args, **kwargs):
        "Save lock and renew expiration date"
        self.id = "%s.%s" % (self.content_type_id, self.object_id)
        self.date_expires = timezone.now() + timezone.timedelta(seconds=EXPIRATION_SECONDS)
        super(Lock, self).save(*args, **kwargs)

    @property
    def has_expired(self):
        return self.date_expires < timezone.now()

    @classmethod
    def is_locked(cls, obj):
        ct_type = ContentType.objects.get_for_model(obj)
        return cls.objects.filter(content_type=ct_type,
                                  object_id=obj.pk,
                                  ).unexpired().exists()
