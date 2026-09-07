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
        message = r"""\/tr \- Translates text to one or multiple languages\. 
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
UA    Ukrainian"""  # noqa: E501
    elif text.endswith("imagine"):
        message = r"""\/imagine \- Creating images using *Ideogram\.ai* engine\. Usage: \/imagine PROMPT
Example: \/imagine Cute kitty plays with yarn ball"""  # noqa: E501
    elif text.endswith("ideogram"):
        message = r"""\/ideogram \- Creating images and typographics using *Ideogram\.ai* engine\. Usage: \/ideogram PROMPT
Example: \/ideogram Cute kitty plays with yarn ball"""  # noqa: E501
    elif (
        text.endswith("creative")
        or text.endswith("balanced")
        or text.endswith("precise")
    ):
        message = r"""Sets the tone of responses for engines that support it\. Each mode will start a new conversation\.
Available values are:
    \• *creative* \(default\)\. More imaginative responses, suitable for creative writing and brainstorming\.
    \• *balanced*\. Balanced mix of information and creativity\.
    \• *precise*\. Concise and factual responses\."""  # noqa: E501
    elif text.endswith("engines"):
        message = r"""\/engines \- You can activate multiple AI engines to set them answering in parallel\. Put their names separated with comma as an argument\.
Example: \/engines gemini,qwen,llama \- all listed engines will respond simultaneously\.
This command persists its value in the user configuration, so it will work until any of following commands applied:
    \• \/llama
    \• \/qwen
    \• \/gemini
    \• \/engines"""  # noqa: E501
    else:
        message = r"""If you need help with bot command, please type the command  
    with \/help prefix, for example *\/help tr*"""

    await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)


async def start_handler(update: Update, context: CallbackContext) -> None:
    logging.info(update.message.text)
    message = r"""Welcome to chat with AI bot\! Here you can get answers from different LLMs, draw images from your prompts with Ideogram\.ai and translate text with DeepL API\.
Supported commands are:

\/help \- Get help on a command\. Usage: \/help COMMAND
\/tr \- Translate text to other language\(s\) using DeepL API
\/imagine \- Generate images using Ideogram\.ai engine
\/ideogram \- Generate images using Ideogram\.ai engine
\/llama \- Switch answers to Meta Llama 4 AI model \(Ollama Cloud\)
\/qwen \- Switch answers to Alibaba Qwen 3\.5 AI model \(Ollama Cloud\)
\/gemini \- Switch answers to Google Gemini AI model
\/engines \- Activates multiple AI engines at once, comma separated list
\/creative \- Set tone of responses to more creative \(Default\)
\/balanced \- Set tone of responses to more balanced
\/precise \- Set tone of responses to more precise"""  # noqa: E501

    await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)
