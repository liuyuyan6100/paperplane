"""Profile use cases keep the existing version and history rules."""

from studio.domain.profiles import ProfilePort


class ProfileService:
    def __init__(self, port: ProfilePort):
        self.port = port

    def listing(self):
        return self.port.listing()

    def get(self, profile_id):
        return self.port.get(profile_id)

    def save(self, values, profile_id=None):
        return self.port.save(values, profile_id)
