import json
import re

# markdown_text = '''[1]: https://contenttool.io/random-sentence-generator \"Random Sentence Generator | Generate 10000+ Sentence - contenttool\"\n[2]: https://www.randomready.com/random-sentence-generator/ \"Random Sentence Generator - Random Ready\"\n[3]: https://randomwordgenerator.com/sentence.php \"Random Sentence Generator — 1000+ Random Sentences\"\n[4]: https://www.thewordfinder.com/random-sentence-generator/ \"Random Sentence Generator - The Word Finder\"\n[5]: https://thestoryshack.com/tools/random-sentence-generator/ \"Random Sentence Generator - Random sentences - The Story Shack\"\n\nI see. There are many online tools that can help you generate random sentences. For example, you can use the **Random Sentence Generator** by contenttool.io[^1] or the **Random Sentence Generator** by randomready.com[^2]. These tools allow you to generate sentences randomly for different purposes and contexts. You can also choose the number and type of sentences you want to generate.\n'''
with open("./lambda/Untitled-2.json", "r") as openfile:
    json_text = json.load(openfile)

markdown_text = json_text["item"]["messages"][1]["adaptiveCards"][0]["body"][0]["text"]

ref_link_pattern = re.compile(r'\[(.*?)\]\:\s?(.*?)\s\"(.*?)\"\n?')

# Find all reference-style links in the Markdown text
ref_links = re.findall(ref_link_pattern, markdown_text)

# Loop through each reference link and create an inline link
for link in ref_links:
    link_label = link[0]
    link_ref = link[1]
    inline_link = f" [[{link_label}]({link_ref})]"
    markdown_text = re.sub(rf'\[\^{link_label}\^\]\[\d+\]', inline_link, markdown_text)
    
# Remove all reference-style links from the Markdown text
markdown_text = re.sub(ref_link_pattern, '', markdown_text)

print(markdown_text)
