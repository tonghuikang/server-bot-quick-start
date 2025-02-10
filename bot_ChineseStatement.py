"""

BOT_NAME="ChineseStatement"; modal deploy --name $BOT_NAME bot_${BOT_NAME}.py; curl -X POST https://api.poe.com/bot/fetch_settings/$BOT_NAME/$POE_ACCESS_KEY

There are three states in the conversation
- Before getting the problem
- After getting the problem, before making a submission
- After making a submission
"""

from __future__ import annotations

import random
import re
from typing import AsyncIterable

import fastapi_poe as fp
from fastapi_poe.types import PartialResponse
from modal import App, Dict, Image, asgi_app

app = App("poe-bot-ChineseStatement")
my_dict = Dict.from_name("dict-ChineseStatement", create_if_missing=True)

with open("chinese_sentences.txt") as f:
    srr = f.readlines()

pattern = r"A\.\d\s"  # e.g. "A.1 "

level_to_statements_and_context = []

context = {}

for line in srr:
    line = line.strip()
    if re.match(pattern, line):
        level_to_statements_and_context.append([])
        continue
    if line == "":
        continue
    if "A." in line:
        depth = line.count(".")
        context[f"{depth}"] = line
        context.pop("【", None)
        context.pop("（", None)
        for nex_depth in range(depth + 1, 10):
            context.pop(f"{nex_depth}", None)
        continue
    if "【" in line:
        context["【"] = line
        context.pop("（", None)
        continue
    if "（" in line:
        context["（"] = line
        continue

    # statement matching
    if "。" not in line and "？" not in line:
        continue
    if "甲" in line or "乙" in line:
        continue
    if "/" in line:
        continue
    if len(line) > 50:
        continue

    level_to_statements_and_context[-1].append((line.strip(), list(context.values())))


TEMPLATE_STARTING_REPLY = """
The statement sampled from HSK level {level} is

# {statement}

Please translate the sentence.
""".strip()

SYSTEM_TABULATION_PROMPT = """
You will test the user on the translation of a Chinese sentence.

The statement is {statement}

You will whether the user's translation captures the full meaning of the sentence.

If the user has  user's translation captures the full meaning of the sentence, end you reply with
- Your translation has captured the full meaning of the sentence.
""".strip()

FREEFORM_SYSTEM_PROMPT = """
You are a patient Chinese language teacher.

You will guide the conversation in ways that maximizes the learning of the Chinese language.

The context of the problem is {context}
"""

PASS_STATEMENT = "I will pass this sentence."

NEXT_STATEMENT = "I want another sentence."


def get_user_level_key(user_id):
    return f"ChineseVocab-level-{user_id}"


def get_conversation_info_key(conversation_id):
    return f"ChineseVocab-statement-{conversation_id}"


def get_conversation_submitted_key(conversation_id):
    return f"ChineseVocab-submitted-{conversation_id}"


class ChineseStatementBot(fp.PoeBot):
    async def get_response(
        self, request: fp.QueryRequest
    ) -> AsyncIterable[fp.PartialResponse]:
        user_level_key = get_user_level_key(request.user_id)
        conversation_info_key = get_conversation_info_key(request.conversation_id)
        conversation_submitted_key = get_conversation_submitted_key(
            request.conversation_id
        )
        last_user_reply = request.query[-1].content
        print(last_user_reply)

        # reset if the user passes or asks for the next statement
        if last_user_reply in (NEXT_STATEMENT, PASS_STATEMENT):
            if conversation_info_key in my_dict:
                my_dict.pop(conversation_info_key)
            if conversation_submitted_key in my_dict:
                my_dict.pop(conversation_submitted_key)

        # retrieve the level of the user
        # TODO(when conversation starter is ready): jump to a specific level
        if last_user_reply in "1234567":
            level = int(last_user_reply)
            my_dict[user_level_key] = level
        elif user_level_key in my_dict:
            level = my_dict[user_level_key]
            level = max(1, level)
            level = min(7, level)
        else:
            level = 1
            my_dict[user_level_key] = level

        # for new conversations, sample a problem
        if conversation_info_key not in my_dict:
            statement, context = random.choice(
                level_to_statements_and_context[level - 1]  # leveling is one indexed
            )
            statement_info = {"statement": statement, "context": context}
            my_dict[conversation_info_key] = statement_info
            yield self.text_event(
                TEMPLATE_STARTING_REPLY.format(
                    statement=statement_info["statement"], level=level
                )
            )
            yield PartialResponse(text=PASS_STATEMENT, is_suggested_reply=True)
            return

        # retrieve the previously cached word
        statement_info = my_dict[conversation_info_key]
        statement = statement_info["statement"]  # so that this can be used in f-string

        # if the submission is already made, continue as per normal
        if conversation_submitted_key in my_dict:
            request.query = [
                {
                    "role": "system",
                    "content": FREEFORM_SYSTEM_PROMPT.format(
                        context=str(statement_info["context"])
                    ),
                }
            ] + request.query
            bot_reply = ""
            async for msg in fp.stream_request(request, "ChatGPT", request.access_key):
                bot_reply += msg.text
                yield msg.model_copy()
            print(bot_reply)
            return

        # otherwise, disable suggested replies
        yield fp.MetaResponse(
            text="",
            content_type="text/markdown",
            linkify=True,
            refetch_settings=False,
            suggested_replies=False,
        )

        request.query = [
            {
                "role": "system",
                "content": SYSTEM_TABULATION_PROMPT.format(statement=statement),
            }
        ] + request.query
        request.temperature = 0
        request.logit_bias = {"2746": -5, "36821": -10}  # "If"  # " |\n\n"

        bot_reply = ""
        async for msg in fp.stream_request(request, "Claude-3.5-Sonnet", request.access_key):
            bot_reply += msg.text
            yield msg.model_copy()

        # make a judgement on correctness
        my_dict[conversation_submitted_key] = True
        if "has captured the full meaning" in bot_reply:
            my_dict[user_level_key] = level + 1
        else:
            my_dict[user_level_key] = level - 1

        # deliver suggsted replies
        yield PartialResponse(
            text="What are other sentences with a similar structure?",
            is_suggested_reply=True,
        )
        yield PartialResponse(text=NEXT_STATEMENT, is_suggested_reply=True)

    async def get_settings(self, setting: fp.SettingsRequest) -> fp.SettingsResponse:
        return fp.SettingsResponse(
            server_bot_dependencies={"Claude-3.5-Sonnet": 1, "GPT-3.5-Turbo": 1},
            introduction_message="Say 'start' to get the sentence to translate.",
        )

