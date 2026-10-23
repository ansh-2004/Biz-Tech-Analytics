# Assessment answers

## Section 1: investigating the slow orders endpoint

### Investigation plan

No original production application, deployment history, or production trace was supplied. The local example is a plausible reconstruction of an N+1 regression, not proof of the original incident's cause. I would investigate in this order:

1. Confirm the affected endpoint, users, response sizes, and when the slowdown started. Compare like-for-like requests before and after the suspected deployment, including users with few and many orders.
2. Capture a request profile with SQL count, repeated query shapes, database time, and total request time. Check database waits and external calls as well: a slow response alone does not establish an N+1 problem.
3. Follow the full execution path, including shared response builders, serializers, model properties, and related-object access. An unchanged view can become slower when a shared serializer starts reading relationships that the queryset does not load.
4. Compare the relevant changes and reproduce the suspected access pattern with controlled data. Here, the hypothesis is that a shared response builder began reading shipping addresses and item products while the caller still fetched only orders. This is a scenario being reconstructed, not an observed commit history.
5. Load the required relationships, compare response contents and query counts, then repeat the request profile. Check user isolation and authentication as part of verifying the fix.

### What the current reconstruction establishes

In `orders/views.py`, both `broken_summary` and `summary` call `build_summary`. The broken path fetches orders without related data. For the measured fixture of 250 orders with two items each, its domain queries break down as follows:

| Access | Queries |
| --- | ---: |
| Fetch orders | 1 |
| Fetch each order's shipping address | 250 |
| Fetch each order's items | 250 |
| Fetch each item's product | 500 |
| Total | 1,001 |

The fixed `summary_queryset` joins `shipping_address` with `select_related()` and uses `Prefetch("items", queryset=OrderItem.objects.select_related("product"))`. This gives two domain queries for this nonempty fixture: orders with addresses, then items with products. Django documents these separate loading strategies in its [QuerySet reference](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-related).

The regression scenario requires no change to the original view; the local demonstration supplies separate broken and fixed views so their querysets can be compared through the same response builder. Shared addresses or products do not remove the broken path's repeated queries across separate model instances.

The cause is Django's lazy relationship loading. Reading `item.product.name` fetches the product if that particular item instance has not loaded it. Django does not share one global object cache across every item. Similarly, `order.items.all()` runs a separate query for each order unless the relation has been prefetched. Adding an index would not remove those repeated round trips.

The fix uses a SQL join for the single address relationship. The prefetch query fetches items for all selected order IDs with an `IN` condition, joins each item's product, and lets Django attach the results to each order's relation cache. The unchanged response builder then reads those cached relations. I kept `.items.all()` here: filtering the related manager again would issue a new query instead of using this prefetch cache. Fetching all these objects still uses memory, so pagination would be a separate change if the response grows much larger.

### Measured evidence

The real [Silk capture](evidence/orders-profile.json) used Django 5.2.18 and PostgreSQL. Both authenticated requests returned identical JSON for 250 orders.

| Measurement | Broken | Fixed |
| --- | ---: | ---: |
| Domain queries | 1,001 | 2 |
| Total queries, including session/user lookup | 1,003 | 4 |
| Request duration | 6,247.22 ms | 45.44 ms |
| Sum of SQL durations | 739.57 ms | 7.47 ms |

The JSON retains timestamps, Silk request IDs, and sample SQL. These are local timings with profiling enabled; Silk overhead contributes to request time, particularly with many queries. This demonstrates the query regression and fix, not the brief's exact 30-second timeout. No delay was added to manufacture that result.

Reproduce with `docker compose exec web python manage.py profile_orders`. Use a clean demo database for the 250-order fixture: the seeder adds missing orders but does not remove extras.

The order tests check query counts at 1, 10, and 250 orders, equal responses, login enforcement, and user isolation. The full suite also exercises the queue and worker failures. Run `docker compose run --rm tests`; the checked-in results are described in [evidence/README.md](evidence/README.md).

## Section 2: background email design

See [DESIGN.md](DESIGN.md) for the queue comparison, required Celery implementation, global rolling limiter, retry policy, recovery behavior, and delivery limitations.

## Section 3

### A. Slow Django Admin with 500,000+ records

A primary-key index does not address every query on an admin page. I would profile the slow request and investigate these three causes:

1. Repeated relationship queries. A `list_display` method or model `__str__()` might fetch a customer for every row. I would set `ModelAdmin.list_select_related = ("customer",)` for that foreign key. For a displayed many-to-many relationship, I would override `ModelAdmin.get_queryset()` and call `prefetch_related()` on the queryset from `super()`. I would also simplify `__str__()` to use local fields where possible. Then I would compare query counts while increasing the displayed rows. The [admin reference](https://docs.djangoproject.com/en/5.2/ref/contrib/admin/#django.contrib.admin.ModelAdmin.list_select_related) describes these queryset hooks.

2. Expensive counts. I would look for slow `COUNT(*)` statements. `ModelAdmin.show_full_result_count = False` removes the extra full-table count, but the default paginator still counts matching rows. If approximate totals are acceptable, I would supply `ModelAdmin.paginator = EstimatedCountPaginator`, a custom `django.core.paginator.Paginator` subclass overriding its `count` property. For an unfiltered list, it could use PostgreSQL's `pg_class.reltuples` estimate; filtered queries need their own count strategy, not that whole-table estimate. I would label totals approximate and handle inaccurate last-page links. PostgreSQL explains why these [statistics can be stale](https://www.postgresql.org/docs/current/planner-stats.html).

3. Search or sorting without a suitable index. I would inspect `search_fields`, `ModelAdmin.ordering`, and the generated SQL with `QuerySet.explain()`. For an exact reference lookup, `search_fields = ("reference__exact",)` can use an index on `reference`; a plain index does not generally fix substring search. For `ordering = ("-created_at", "-id")`, I would add `models.Index(fields=["-created_at", "-id"])` to `Meta.indexes` and migrate it. I would verify the actual plan before and after, using Django's [explain() documentation](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#explain).

### B. Pagination

For an API returning 10,000 records, I would first reduce each response to a bounded page, perhaps 50 records. That reduces download time and phone memory use. The pagination choice then affects how scrolling behaves as the dataset grows.

With OFFSET, page 101 might use `LIMIT 50 OFFSET 5000`. The database still processes the skipped rows, even when an index supplies the ordering. Deep pages therefore become more expensive and can leave a mobile user waiting longer with each scroll. PostgreSQL documents this [OFFSET cost](https://www.postgresql.org/docs/current/queries-limit.html). Concurrent changes also move row positions: in a newest-first list, an insertion before the next offset can repeat a record, while a deletion can make the next request skip one. A deterministic ordering fixes ties, but not these shifts.

For mobile infinite scroll, I would choose cursor pagination over an immutable, unique, indexed key. With descending numeric IDs, the next page can use `filter(id__lt=last_id).order_by("-id")[:50]`. The index lets the database seek past the cursor rather than scan every earlier page. With tenant filtering, I would consider a matching `(tenant_id, id)` index. Django REST framework's `CursorPagination` supports an explicit `ordering = "-id"`; its [documentation](https://www.django-rest-framework.org/api-guide/pagination/#cursorpagination) explains the ordering requirements.

Newer inserts no longer shift that boundary, and deleting an earlier row does not change the next position. This is still not snapshot consistency: records can disappear, values can change, and changing filter membership affects what later requests see.

I would choose OFFSET for smaller, fairly stable lists where users need numbered pages and direct jumps. I would choose cursors for large, changing feeds and sequential scrolling. Cursors sacrifice arbitrary page jumps, and exact totals still require separate counting work; neither pagination style makes counts free.
