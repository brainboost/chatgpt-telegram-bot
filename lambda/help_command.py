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
UA    Ukrainian"""
    elif text.endswith("imagine"):
        message = r"""\/imagine \- Creating images using *Ideogram\.ai* engine\. Usage: \/imagine PROMPT
Example: \/imagine Cute kitty plays with yarn ball"""
    elif text.endswith("ideogram"):
        message = r"""\/ideogram \- Creating images and typographics using *Ideogram\.ai* engine\. Usage: \/ideogram PROMPT
Example: \/ideogram Cute kitty plays with yarn ball"""
    elif text.endswith(("creative", "balanced", "precise")):
        message = r"""Sets the tone of responses for providers that support it\. Each mode will start a new conversation\.
Available values are:
    \• *creative* \(default\)\. More imaginative responses, suitable for creative writing and brainstorming\.
    \• *balanced*\. Balanced mix of information and creativity\.
    \• *precise*\. Concise and factual responses\."""
    elif text.endswith(("llama", "qwen", "gemini")):
        message = r"""Sets which provider starts answering your chat messages\.
If that provider is unavailable, the bot falls back automatically\.
    \• \/gemini \- falls back to Qwen, then Llama \(default\)
    \• \/qwen \- falls back to Llama
    \• \/llama \- no further fallback
The choice is remembered for your next conversations\."""
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
\/gemini \- Answer with Google Gemini \(default\)
\/qwen \- Answer with Alibaba Qwen 3\.5 \(Ollama Cloud\)
\/llama \- Answer with Meta Llama 4 \(Ollama Cloud\)
\/creative \- Set tone of responses to more creative \(Default\)
\/balanced \- Set tone of responses to more balanced
\/precise \- Set tone of responses to more precise"""

    await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)
