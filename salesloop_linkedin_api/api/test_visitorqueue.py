from datetime import datetime, timedelta
from os import environ

from application.integrations.visitorqueue.visitorqueue import VisitorQueue
from tests.lib import validate_json_schema as original_validate_json_schema, logger

start_date = datetime.utcnow() - timedelta(days=100)
end_date = datetime.utcnow()

visitorqueue = VisitorQueue(environ["VQ_DEBUG_API_KEY"])
data_views = visitorqueue.data_views()


def validate_json_schema(*args, **kwargs):
    return original_validate_json_schema(*args, **kwargs, subdirectory="visitorqueue")


def test_data_views():
    logger.debug(data_views)
    validate_json_schema("data_views", data_views)


def test_leads():
    site_id = data_views[0]["id"]
    leads = visitorqueue.leads(site_id)
    validate_json_schema("leads", leads)

    leads = visitorqueue.leads(site_id, start_date=start_date, end_date=end_date, per_page=20)
    logger.debug(leads)
    assert len(leads) > 0
    validate_json_schema("leads", leads)
