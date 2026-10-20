"""Project only reviewable topic fields while reusing the original durable task."""


def project(plan):
    context = plan.get("context") or {}
    profile = context.get("profile") or {}
    return {
        "id": plan["id"],
        "status": plan["status"],
        "error": plan["error"],
        "idea": context.get("idea", plan.get("idea", "")),
        "profile_name": profile.get("name", plan.get("profile_name", "")),
        "profile_version": profile.get("version", plan.get("profile_version")),
        "candidates": plan["candidates"],
        "selected_index": plan["selected_index"],
        "selected_post_id": plan["selected_post_id"],
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
    }


class LegacyTopics:
    def __init__(self, studio):
        self.studio = studio

    def listing(self):
        return [project(plan) for plan in self.studio.topics.list()]

    def get(self, plan_id):
        return project(self.studio.topics.get(plan_id))

    def create(self, values):
        return project(self.studio.topics.create({**values, "reference_ids": []}))

    def retry(self, plan_id):
        return project(self.studio.topics.retry(plan_id))

    def select(self, plan_id, index):
        result = self.studio.topics.select(plan_id, {"index": index})
        return {"plan": project(result["plan"]), "post": result["post"]}
