"""

BOT_NAME="FlowchartPlotter"; modal deploy --name $BOT_NAME bot_${BOT_NAME}.py; curl -X POST https://api.poe.com/bot/fetch_settings/$BOT_NAME/$POE_ACCESS_KEY

Test message:
echo z > a.txt
cat a.txt

"""

from __future__ import annotations

import glob
import os
import re
import textwrap
import subprocess
import uuid
from typing import AsyncIterable

from fastapi_poe import MetaResponse, PoeBot, make_app
from fastapi_poe.types import (
    PartialResponse,
    QueryRequest,
    SettingsRequest,
    SettingsResponse,
)
from modal import Image, Stub, asgi_app

puppeteer_config_json_content = """{
  "args": ["--no-sandbox"]
}
"""

INTRODUCTION_MESSAGE = """
This bot will draw [mermaid diagrams](https://docs.mermaidchart.com/mermaid/intro).

A mermaid diagram will look like this

````
```mermaid
graph TD
    A[Client] --> B[Load Balancer]
```
````

Try copying the above, paste it, and reply.
""".strip()


RESPONSE_MERMAID_DIAGRAM_MISSING = """
No mermaid diagrams were found in your previous message.

A mermaid diagram will look like

````
```mermaid
graph TD
    A[Client] --> B[Load Balancer]
```
````

See examples [here](https://docs.mermaidchart.com/mermaid/intro).
""".strip()


class FlowChartPlotterBot(PoeBot):
    async def get_response(
        self, request: QueryRequest
    ) -> AsyncIterable[PartialResponse]:

        # disable suggested replies
        yield MetaResponse(
            text="",
            content_type="text/markdown",
            linkify=True,
            refetch_settings=False,
            suggested_replies=False,
        )

        while request.query:
            last_message = request.query[-1].content
            print(last_message)
            if "```mermaid" in last_message:
                break
            request.query.pop()
            if len(request.query) == 0:
                yield PartialResponse(text=RESPONSE_MERMAID_DIAGRAM_MISSING)
                return

        random_uuid = uuid.uuid4()

        with open("puppeteer-config.json", "w") as f:
            f.write(puppeteer_config_json_content)

        with open(f"{random_uuid}.md", "w") as f:
            f.write(last_message)

        # svg is not supported
        command = f"mmdc -p puppeteer-config.json -i {random_uuid}.md -o {random_uuid}-output.png"
        yield PartialResponse(text="Drawing ...")

        _ = subprocess.check_output(command, shell=True, text=True)

        filenames = list(glob.glob(f"{random_uuid}-output-*.png"))

        if len(filenames) == 0:
            yield PartialResponse(
                text=RESPONSE_MERMAID_DIAGRAM_MISSING, is_replace_response=True
            )
            return

        for filename in filenames:
            print("filename", filename)
            with open(filename, "rb") as f:
                file_data = f.read()

            attachment_upload_response = await self.post_message_attachment(
                message_id=request.message_id,
                file_data=file_data,
                filename=filename,
                is_inline=True,
            )
            yield PartialResponse(
                text=f"\n\n![flowchart][{attachment_upload_response.inline_ref}]\n\n",
                is_replace_response=True,
            )

    async def get_settings(self, setting: SettingsRequest) -> SettingsResponse:
        return SettingsResponse(introduction_message=INTRODUCTION_MESSAGE)

