"""Article editing delegates atomic version/history semantics to its repository."""

from studio.domain.articles import ArticleRepository


class ArticleService:
    def __init__(self, repository: ArticleRepository):
        self.repository = repository

    def listing(self, scope="active"):
        return self.repository.listing(scope)

    def manage(self, article_id, action, version):
        return self.repository.manage(article_id, action, version)

    def storage(self, clean=False):
        return self.repository.storage(clean)

    def get(self, article_id):
        return self.repository.get(article_id)

    def save(self, values, article_id=None):
        return self.repository.save(values, article_id)
