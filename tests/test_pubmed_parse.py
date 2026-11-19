import pytest

from proef.adapters.databases.pubmed import parse_efetch

XML = b"""<?xml version="1.0"?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>
<Journal><Title>J Test</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>EGFR <i>T790M</i> resistance</ArticleTitle>
<Abstract><AbstractText Label="A">First.</AbstractText><AbstractText>Second.</AbstractText></Abstract>
<AuthorList><Author><LastName>Lee</LastName><Initials>A</Initials></Author></AuthorList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


def test_efetch_xml_is_parsed_into_articles():
    a = parse_efetch(XML)[0]
    assert (a["pmid"], a["title"], a["abstract"], a["journal"], a["pub_date"], a["authors"]) == (
        "1", "EGFR T790M resistance", "First. Second.", "J Test", "2024", "Lee A")


def test_xml_with_entity_declarations_is_refused():
    with pytest.raises(ValueError):
        parse_efetch(b'<!DOCTYPE x [<!ENTITY a "b">]><PubmedArticleSet/>')
