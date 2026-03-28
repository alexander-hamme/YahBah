import asyncio

from sqlalchemy import select

from yahbah.browser.greenhouse import GreenhouseExtractor
from yahbah.browser.manager import BrowserRegistry
from yahbah.db.models import ApplicantProfile
from yahbah.db.session import AsyncSessionLocal
from yahbah.llm.field_mapper import FieldMapper
from yahbah.schemas import FormSchema, FormField


async def test():
    # TODO check if all these awaits are the right way to do it
    reg = BrowserRegistry.instance()
    await reg.start()
    page = await reg.open_page("test-run", "https://job-boards.greenhouse.io/paradigminccareersopenpositions/jobs/4647304005")
    extractor = GreenhouseExtractor(page)
    schema, desc = await extractor.extract()
    for f in schema.fields:
        print(f)

    # TODO check fields against expected

    await test_fill(schema)

    await reg.stop()

async def test_fill(schema):
    async with AsyncSessionLocal() as s:
      profile = (await s.execute(select(ApplicantProfile).limit(1))).scalar_one()

    # schema = FormSchema(
    #   fields=[
    #       FormField(label="First name", field_type="text", required=True),
    #       FormField(label="Last name", field_type="text", required=True),
    #       FormField(label="Email", field_type="email", required=True),
    #       FormField(label="Resume", field_type="file", required=True),
    #   ],
    #   page_url="https://example.com",
    #   page_title="Test",
    # )
    mapper = FieldMapper()
    result = await mapper.map(schema, profile)
    for m in result.field_mappings:
      print(m)


if __name__ == "__main__":
    asyncio.run(test())