import requests
import discord
import openai
import os
from dotenv import load_dotenv
from discord.ext import tasks
from bs4 import BeautifulSoup
import datetime
from typing import Annotated
from functools import reduce
Articles = Annotated[dict, "{ 'message_id': {'article_id', 'contexts'} }"]
Prompt = Annotated[dict, "{'role', 'content' }"]


load_dotenv()
CHANNEL_ID = os.getenv("CHANNEL_ID")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# DURATION_HOURS = 3
NUM_ARTICLE_PER_HOURS = 2

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.typing = False
intents.presences = False

client = discord.Client(intents=intents)

HN_URL_PREF = "https://hacker-news.firebaseio.com/v0/item/"

openai.api_key = OPENAI_API_KEY

SYS_PROMPT: Prompt = {"role": "system",
                      "content":  "この会話では、このあと送る英語の文章を要約して伝えてくださいにゃ。語尾は'にゃでお願いします'"}
PRE_MESSAGE: Prompt = {"role": "user",
                       "content": "今までの内容を日本語で要約してくださいにゃ。語尾は'にゃ'でお願いしますにゃ。\n"}

articles_today: Articles = {}
today = datetime.date.today()
tz_jst = datetime.timezone(
    datetime.timedelta(hours=9))
times = [
    datetime.time(hour=8, tzinfo=tz_jst),
    datetime.time(hour=12, tzinfo=tz_jst),
    datetime.time(hour=16, tzinfo=tz_jst)
]


# TODO: make funciton and can be trigger by user commands
@tasks.loop(time=times)
async def fetch_hacker_news_top_stories():
    global articles_today
    url = "https://hacker-news.firebaseio.com/v0/topstories.json"

    if today != datetime.date.today():
        articles_today = {}

    try:
        response = requests.get(url)
        response.raise_for_status()
        top_stories = response.json()
        cnt = 0
        for article_id in top_stories:

            # abort if number reaches
            if cnt == NUM_ARTICLE_PER_HOURS:
                break

            # skip already published article today
            if reduce(lambda b, a: a and (b or (a["article_id"] == article_id)), articles_today.values(), False):
                continue

            # get article info
            article_info_url = HN_URL_PREF + str(article_id) + ".json"
            response = requests.get(article_info_url)
            response.raise_for_status()
            article_info = response.json()
            article_title = article_info["title"]
            article_url = article_info["url"]

            # get top comment
            article_top_comment = article_info["kids"][0]
            comment_url = HN_URL_PREF + str(article_top_comment) + ".json"
            response = requests.get(comment_url)
            response.raise_for_status()
            comment_items = response.json()
            comment = comment_items["text"]
            soup = BeautifulSoup(comment, 'lxml')
            comment_text = soup.get_text()

            # cut comment into pieces less than 2000 characters
            l = 0
            r = max(len(comment_text) // 3, 2000)
            messages = [SYS_PROMPT]
            if 0 < len(comment_text):
                while l < len(comment_text):
                    messages.append(
                        {"role": "user", "content": comment_text[l:r]})
                    r = max(len(comment_text), r + 2000)
                    l = max(len(comment_text), l + 2000)
                messages.append(
                    PRE_MESSAGE)
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo", messages=messages, temperature=0)
                summary = response.choices[0]["message"]["content"].strip()
            res_text = f"{article_title} {article_url}\n{summary}"

            target_channel = None
            # TODO: more sophisticated way to find channel?
            for guild in client.guilds:
                for channel in guild.channels:
                    if str(channel.id) == CHANNEL_ID:
                        target_channel = channel
                        break
                if target_channel:
                    break
            if target_channel is None:
                raise "No channel specified"

            # save comments
            sent_message = await target_channel.send(res_text)
            articles_today[sent_message.id] = {
                "article_id":  article_id, "contexts": messages}
            cnt += 1

    except Exception as e:
        print("Error : ", e)


@client.event
async def on_ready():
    print(f"{client.user} has connected to Discord!")
    fetch_hacker_news_top_stories.start()


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # reply if message is tied with active article thread
    # TODO: handle no starter message
    if (message.channel.starter_message is not None) and (message.channel.starter_message.id in articles_today):
        # TODO: handle commands before heneral reply ex. comments, body
        prompt = message.content
        if "> " in message.content:
            prompt = prompt.split("> ")[-1]
        # TODO: handle no key error
        messages = articles_today[message.channel.starter_message.id]["contexts"]

        messages.append({"role": "user", "content": prompt})
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo", messages=messages)
        messages.append(response.choices[0]["message"])
        await message.channel.send(response.choices[0]["message"]["content"].strip())

client.run(DISCORD_TOKEN)
