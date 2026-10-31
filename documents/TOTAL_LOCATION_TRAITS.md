# Location traits for configured totals

Set `attributes.location_traits` on an active Store foundation reference to a list of trait codes, for example `{"location_traits": ["North", "Mall"]}`. Set `location_traits` on a total definition to the trait codes that should receive it. An empty list applies to every store; a store matching multiple selected traits receives the total once.

When any effective definition specifies traits, calculation requires an active Store reference with valid trait data. A missing `location_traits` attribute means that the store has no trait memberships. Combined totals require their referenced revisions to apply to the store as well. Membership is read from current foundation data when the store day is calculated; historical membership is not versioned.

Changing a store's trait membership or active state marks its calculated store days stale and supersedes pending closure requests. Adding an enabled audit rule invalidates calculated days globally; a new total-definition revision invalidates days in its effective date range. Closed days remain closed but must be reopened and retotaled if affected.
