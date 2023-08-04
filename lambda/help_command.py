import logging

from telegram import (
    Update,
    constants,
)
from telegram.ext import CallbackContext

logging.basicConfig()
logging.getLogger().setLevel("INFO")


async def help_handler(update: Update, context: CallbackContext) -> None:
    logging.info(update.message.text)
    text = update.message.text.strip().lower()
    if text.endswith("tr"):
        message = """\/tr \- Translates text to one or multiple languages\. 
Target language can be set either clicking menu button *or* typing in language code\(s\) by hands\. 
You also can set languages separated with commas directly in the \/tr command, like this: \/tr pl,ru - in this case bot skips the question about language.  
Several language codes must be separated by comma\. Example: _pl,ru,en\-gb_

Supported languages are:

BG    Bulgarian
ZH    Chinese
CS    Czech
DA    Danish
NL    Dutch
EN\-GB    English UK
EN\-US    English US
ET    Estonian
FI    Finnish
FR    French
DE    German
EL    Greek
HU    Hungarian
ID    Indonesian
IT    Italian
JP    Japanese
KO    Korean
LV    Latvian
LT    Lithuanian
NO    Norwegian
PL    Polish
PT    Portuguese
RO    Romanian
RU    Russian
SK    Slovak
SL    Slovenian
ES    Spanish
SV    Swedish
TR    Turkish
UA    Ukrainian"""
    elif text.endswith("imagine"):
        message = """\/imagine \- Creating images using *DALL\-E 2* AI engine\. Usage: \/imagine PROMPT
Example: \/imagine Cute kitty plays with yarn ball"""
    elif (
        text.endswith("creative")
        or text.endswith("balanced")
        or text.endswith("precise")
    ):
        message = """Sets the tone of responses to the Bing AI engine\. Has no effect on other engines\. Each mode will start a new conversation
Available values are: 
    \• *creative* (default)\. This mode is for when you want to have fun and explore your imagination with me\. I can generate content such as poems, stories, jokes, images, and more\. I can also help you improve your own content by rewriting, optimizing, or adding details\. I use a friendly and informal tone in this mode\.
    \• *balanced*\. This mode is for when you want to have a balanced conversation with me\. I can provide information, facts, opinions, and suggestions based on your queries\. I can also chat with you about various topics and interests\. I use a polite and neutral tone in this mode\.
    \• *precise*\. This mode is for when you want to get precise and accurate answers from me\. I can perform web searches, calculations, conversions, and other tasks that require logic and reasoning\. I can also generate images based on your specifications\. I use a concise and formal tone in this mode\."""
    elif text.endswith("engines"):
        message = """\/set\_engines \- You can activate multiple AI engines to set them answering in parallel\. Put their names separated with comma as an argument\.
Example: \/set\_engines bing,bard,chatgpt,llama \- all AI engines will respond simultaneously\.
This command persist it's value in the user configuration, so it will work until any of following commands applied: 
    \• \/bing
    \• \/bard
    \• \/chatgpt
    \• \/llama
    \• \/set\_engines"""
    else:
        message = """If you need help with bot command, please type the command  
    with \/help prefix, for example *\/help tr*"""

    await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)


async def start_handler(update: Update, context: CallbackContext) -> None:
    logging.info(update.message.text)
    message = """Welcome to chat with AI bot\! Here you can get answers from different LLMs, draw images from your prompts with DALL\-E 2 and translate text with DeepL API\. 
Supported commands are:

\/help \- Get help on a command\. Usage: \/help COMMAND
\/tr \- Translate text to other language\(s\) using DeepL API
\/imagine \- Generate images using DALL\-E 2 engine
\/bing \- Switch answers to Bing AI model
\/bard \- Switch answers to Google Bard AI model
\/chatgpt \- Switch answers to OpenAI ChatGPT model
\/llama \- Switch answers to Meta LLama2 AI model
\/set\_engines \- Activates multiple AI engines at once, comma separated list
\/creative \- Set tone of responses to more creative on Bing model \(Default\)
\/balanced \- Set tone of responses to more balanced
\/precise \- Set tone of responses to more precise"""

    await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)
