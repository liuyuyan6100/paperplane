"""Reuse the original profile store without copying or migrating profile data."""

from studio import profiles


class LegacyProfiles:
    def __init__(self, studio):
        self.studio = studio

    def listing(self):
        return [{"id": row["id"], "name": row["name"]} for row in profiles.listing(self.studio)]

    def get(self, profile_id):
        return profiles.get(self.studio, profile_id)

    def save(self, values, profile_id=None):
        return profiles.save(self.studio, values, profile_id)
