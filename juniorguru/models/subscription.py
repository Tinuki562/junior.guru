from collections import Counter
from datetime import date
from enum import StrEnum, unique
from functools import wraps
import math
from numbers import Number
from typing import Callable, Iterable, Self

from peewee import BooleanField, CharField, DateField, DateTimeField, fn, Case, SQL
from juniorguru.lib.charts import month_range

from juniorguru.models.base import BaseModel, check_enum


LEGACY_PLANS_DELETED_ON = date(2023, 2, 24)


@unique
class SubscriptionActivityType(StrEnum):
    TRIAL_START = 'trial_start'
    TRIAL_END = 'trial_end'
    ORDER = 'order'
    DEACTIVATION = 'deactivation'


@unique
class SubscriptionInterval(StrEnum):
    MONTH = 'month'
    YEAR = 'year'


@unique
class SubscriptionType(StrEnum):
    FREE = 'free'
    FINAID = 'finaid'
    INDIVIDUAL = 'individual'
    TRIAL = 'trial'
    PARTNER = 'partner'
    STUDENT = 'student'


def uses_data_from_subscriptions(default: Callable=None) -> Callable:
    def decorator(class_method: Callable) -> Callable:
        @wraps(class_method)
        def wrapper(cls, date) -> None | Number:
            if is_missing_subscriptions_data(date):
                return default() if default is not None else None
            return class_method(cls, date)
        return wrapper
    return decorator


def is_missing_subscriptions_data(month: date) -> bool:
    if month < LEGACY_PLANS_DELETED_ON:
        return True
    if month_range(month) == month_range(LEGACY_PLANS_DELETED_ON):
        return True
    return False


class SubscriptionActivity(BaseModel):
    class Meta:
        indexes = (
            (('type', 'account_id', 'happened_on'), True),
        )

    type = CharField(constraints=[check_enum('type', SubscriptionActivityType)])
    account_id = CharField(index=True)
    account_has_feminine_name = BooleanField()
    happened_on = DateField()
    happened_at = DateTimeField(index=True)
    order_coupon = CharField(null=True)
    subscription_interval = CharField(null=True, constraints=[check_enum('subscription_interval', SubscriptionInterval)])
    subscription_type = CharField(null=True, constraints=[check_enum('subscription_type', SubscriptionType)])

    @classmethod
    def add(cls, **kwargs):
        unique_key_fields = cls._meta.indexes[0][0]
        conflict_target = [getattr(cls, field) for field in unique_key_fields]
        update = {field: kwargs[field]
                  for field, value in kwargs.items()
                  if ((value is not None) and
                      (field not in unique_key_fields))}
        update[cls.happened_at] = Case(None,
                                       [(cls.happened_at < kwargs['happened_at'], kwargs['happened_at'])],
                                       cls.happened_at)
        insert = cls.insert(**kwargs) \
            .on_conflict(action='update',
                         update=update,
                         conflict_target=conflict_target)
        insert.execute()

    @classmethod
    def mark_trials(cls) -> None:
        # The 'order' activity happening on the same day as 'trial_end' activity
        # marks subscription type of the whole trial.
        also_cls = cls.alias()
        cte = cls.select(cls.account_id, cls.happened_on, cls.subscription_type) \
            .join(also_cls, on=((cls.account_id == also_cls.account_id) &
                                (cls.happened_on == also_cls.happened_on))) \
            .where(cls.type == SubscriptionActivityType.ORDER,
                   also_cls.type == SubscriptionActivityType.TRIAL_END) \
            .cte('new_subscription_type', columns=('account_id', 'happened_on', 'subscription_type'))
        cls.update(subscription_type=SQL('new_subscription_type.subscription_type')) \
            .with_cte(cte) \
            .from_(cte) \
            .where(cls.account_id == SQL('new_subscription_type.account_id'),
                   cls.happened_on <= SQL('new_subscription_type.happened_on')) \
            .execute()

        # Mark trials of individual subscriptions as true trials
        also_cls = cls.alias()
        to_update = cls.select(cls.id) \
            .join(also_cls, on=(cls.account_id == also_cls.account_id)) \
            .where(also_cls.type == SubscriptionActivityType.TRIAL_END,
                   also_cls.subscription_type == SubscriptionType.INDIVIDUAL,
                   ((cls.type == SubscriptionActivityType.ORDER) & (cls.happened_on < also_cls.happened_on)) |
                   ((cls.type == SubscriptionActivityType.TRIAL_END) & (cls.happened_on == also_cls.happened_on)) |
                   ((cls.type == SubscriptionActivityType.TRIAL_START) & (cls.happened_on < also_cls.happened_on)))
        cls.update(subscription_type=SubscriptionType.TRIAL) \
            .where(cls.id.in_(to_update)) \
            .execute()

    @classmethod
    def total_count(cls) -> int:
        return cls.select().count()

    @classmethod
    def listing(cls, date: date) -> Iterable[Self]:
        latest_at = fn.max(cls.happened_at).alias('latest_at')
        latest = cls.select(cls.account_id, latest_at) \
            .where(cls.happened_on <= date) \
            .group_by(cls.account_id)
        return cls.select(cls) \
            .join(latest, on=((cls.account_id == latest.c.account_id) &
                              (cls.happened_at == latest.c.latest_at)))

    @classmethod
    def active_listing(cls, date: date) -> Iterable[Self]:
        return cls.listing(date) \
            .where(cls.type != SubscriptionActivityType.DEACTIVATION)

    @classmethod
    def active_count(cls, date: date) -> int:
        return cls.active_listing(date).count()

    @classmethod
    @uses_data_from_subscriptions()
    def active_individuals_count(cls, date: date) -> int | None:
        return cls.active_listing(date) \
            .where(cls.subscription_type == SubscriptionType.INDIVIDUAL) \
            .count()

    @classmethod
    @uses_data_from_subscriptions()
    def active_individuals_yearly_count(cls, date: date) -> int | None:
        return cls.active_listing(date) \
            .where(cls.subscription_type == SubscriptionType.INDIVIDUAL,
                   cls.subscription_interval == SubscriptionInterval.YEAR) \
            .count()

    @classmethod
    @uses_data_from_subscriptions(default=dict)
    def active_subscription_type_breakdown(cls, date: date) -> dict[str, int]:
        counter = Counter([activity.subscription_type for activity in cls.active_listing(date)])
        if None in counter:
            raise ValueError("There are members whose latest activity is without subscription type, "
                             f"which can happen only if they're from before {LEGACY_PLANS_DELETED_ON}. "
                             "But then they should be filtered out by the clause HAVING type != deactivation. "
                             "It's very likely these members are deactivated, but it's not reflected in the data. "
                             "See if we shouldn't observe more activities in the ACTIVITY_TYPES_MAPPING.")
        return {subscription_type.value: counter[subscription_type]
                for subscription_type in SubscriptionType}

    @classmethod
    def active_women_count(cls, date: date) -> int:
        return cls.active_listing(date) \
            .where(cls.account_has_feminine_name == True) \
            .count()

    @classmethod
    def active_women_ptc(cls, date: date) -> int:
        if count := cls.active_count(date):
            return math.ceil((cls.active_women_count(date) / count) * 100)
        return 0

    @classmethod
    def signups(cls, date: date) -> Iterable[Self]:
        from_date, to_date = month_range(date)
        return cls.select(fn.min(cls.happened_at)) \
            .where(cls.happened_on <= to_date) \
            .group_by(cls.account_id) \
            .having(cls.happened_on >= from_date)

    @classmethod
    def signups_count(cls, date: date) -> int:
        return cls.signups(date).count()

    @classmethod
    @uses_data_from_subscriptions()
    def individuals_signups_count(cls, date: date) -> int:
        return cls.signups(date) \
            .where(cls.subscription_type == SubscriptionType.INDIVIDUAL) \
            .count()

    # @classmethod
    # def quits(cls, date):
    #     from_date, to_date = month_range(date)
    #     return cls.select(cls, fn.max(cls.end_on)) \
    #         .group_by(cls.account_id) \
    #         .having(cls.end_on >= from_date, cls.end_on <= to_date) \
    #         .order_by(cls.end_on)

    # @classmethod
    # def quits_count(cls, date):
    #     return cls.quits(date).count()

    # @classmethod
    # def individuals_quits(cls, date):
    #     return cls.quits(date).where(cls.type == SubscribedPeriodType.INDIVIDUALS)

    # @classmethod
    # def individuals_quits_count(cls, date):
    #     return cls.individuals_quits(date).count()

    # @classmethod
    # def churn_ptc(cls, date):
    #     from_date = month_range(date)[0]
    #     churn = cls.quits_count(date) / (cls.count(from_date) + cls.signups_count(date))
    #     return churn * 100

    # @classmethod
    # def individuals_churn_ptc(cls, date):
    #     from_date = month_range(date)[0]
    #     churn = cls.individuals_quits_count(date) / (cls.individuals_count(from_date) + cls.individuals_signups_count(date))
    #     return churn * 100

    # @classmethod
    # def active_duration_avg(cls, date: date) -> int:
    #     earliest_at = fn.min(cls.happened_at)
    #     duration_sec = (fn.unixepoch(date) - fn.unixepoch(earliest_at))
    #     duration_mo = (duration_sec / 60 / 60 / 24 / 30).alias('duration_mo')

    #     rows = cls.select(duration_mo) \
    #         .where(cls.happened_at <= date) \
    #         .group_by(cls.account_id) \
    #         .dicts()
    #     if durations := [row['duration_mo'] for row in rows]:
    #         return sum(durations) / len(durations)
    #     return 0


class SubscriptionCancellation(BaseModel):
    name = CharField()
    email = CharField()
    expires_on = DateField(null=True)
    reason = CharField()
    feedback = CharField(null=True)


class SubscriptionReferrer(BaseModel):
    account_id = CharField()
    name = CharField()
    email = CharField()
    created_on = DateField()
    value = CharField()
    type = CharField(index=True)
    is_internal = BooleanField(index=True)


class SubscriptionMarketingSurvey(BaseModel):
    account_id = CharField()
    name = CharField()
    email = CharField()
    created_on = DateField()
    value = CharField()
    type = CharField(index=True)
