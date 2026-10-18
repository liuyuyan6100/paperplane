"""Compatibility adapter; retains existing SQLite transactions and revision history."""

from studio import profiles


class LegacyArticleRepository:
    def __init__(self, studio):
        self.studio = studio

    def listing(self):
        return self.studio.db.rows(
            "SELECT id,title,summary,stage,version,updated_at,profile_id FROM posts "
            "WHERE archived=0 ORDER BY updated_at DESC,rowid DESC"
        )

    def get(self, article_id):
        return self.studio.post(article_id)

    def save(self, values, article_id=None):
        return self.studio.save_post(values, article_id)

    def profiles(self):
        return [{"id": row["id"], "name": row["name"]} for row in profiles.listing(self.studio)]
