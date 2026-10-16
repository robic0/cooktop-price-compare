import requests
from bs4 import BeautifulSoup
import json

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
url = 'https://mediamarkt.pl/pl/category/plyty-indukcyjne-do-zabudowy-25591.html'
r = requests.get(url, headers=headers, timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')

next_link = soup.find('link', rel='next')
print('Next link in head:', next_link.get('href') if next_link else None)

pagination_links = [a.get('href') for a in soup.find_all('a') if 'page=' in (a.get('href') or '')]
print('Pagination links sample:', pagination_links[:5])

for s in soup.find_all('script', type='application/ld+json'):
    try:
        data = json.loads(s.string)
        if isinstance(data, dict) and data.get('@type') == 'ItemList':
            print('Page 1 items count:', len(data.get('itemListElement', [])))
    except Exception as e:
        print("Error:", e)

# Test page 2 URL
r2 = requests.get('https://mediamarkt.pl/pl/category/plyty-indukcyjne-do-zabudowy-25591.html?page=2', headers=headers, timeout=15)
soup2 = BeautifulSoup(r2.text, 'html.parser')
next_link2 = soup2.find('link', rel='next')
print('Page 2 next link:', next_link2.get('href') if next_link2 else None)
for s in soup2.find_all('script', type='application/ld+json'):
    try:
        data = json.loads(s.string)
        if isinstance(data, dict) and data.get('@type') == 'ItemList':
            print('Page 2 items count:', len(data.get('itemListElement', [])))
    except:
        pass
