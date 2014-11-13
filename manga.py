from bs4 import BeautifulSoup
from re import search
from os import makedirs
from os.path import dirname, exists, isfile, getsize
from sys import getfilesystemencoding
from urllib.request import build_opener
from threading import Thread, Lock
from queue import Queue
from json import load, dump
from collections import MutableMapping, namedtuple
from time import sleep

encoding = getfilesystemencoding()

filesystemLock = Lock()

def ensure_dir(f):
    with filesystemLock:
        d = dirname(f)
        if not exists(d):
            makedirs(d)

Option = namedtuple('Option', ['text', 'value'])

"""
A JSONDict is a dictionary that automatically saves itself when modified.
Used to automatically save changes to the cache/index.
"""
class JSONDict(MutableMapping):
    
    def new(self):
        self.store = dict()
        with open(self.filename, 'w') as f:
            dump(self.store, f)

    def __init__(self, filename, reindex=False):
        self.filename = filename
        ensure_dir(self.filename)
        if reindex:
            self.new()
            return
        try:
            with open(self.filename, 'r') as f:
                self.store = load(f)
        except:
            self.new()

    def __getitem__(self, key):
        return self.store[key]

    def __setitem__(self, key, value):
        self.store[key] = value
        with open(self.filename, 'w') as f:
            dump(self.store, f)

    def __delitem__(self, key):
        del self.store[key]
        with open(self.filename, 'w') as f:
            dump(self.store, f)

    def __iter__(self):
        return iter(self.store)

    def __len__(self):
        return len(self.store)

# Build up a user-agent header to spoof as a normal browser
opener = build_opener()
opener.addheaders = [('User-agent', 'Mozilla/5.0')]

""" 
Repeatedly tries to open the specified URL.
After 10 failed attempts it will raise an exception.
"""
def repeat_urlopen(my_str):
    for i in range(10):
        try:
            return opener.open(my_str)
        except Exception as e:
            print(e)
            sleep(1)
    raise Exception('Unable to resolve URL (' + my_str + ') after 10 attempts.')

class series(Thread):
    def __init__(self, site, title, sort=True, digits=3, workers=3, reindex=False):
        """
        site: specifies the root url for the manga on a site

        title: specifes the 'directory' for a particular manga on the site

        sort: specifies whether or not to download the chapters in order

        digits: specifies the length of the zero-padded chapter and page

            e.g.: digits=3 filenames would look something like '000.000'

            digits=3 handles 0-999 chapters,
            one 'extra chapter' digit,
            and 0-99 pages per chapter

            e.g.: Nisekoi c105.5 p.2 would have the filename '105.502'
                           ^^^-----------------------chapter--^^^
                               ^---------------------extra chptr--^
                                   ^-----------------page number---^^

                  Nisekoi c122 p.12 would be '122.012'
                           ^^^-------chapter--^^^
                              ^------extra(none)--^
                                 ^^--page number---^^

        workers: specifies the maximum number of simultaneous file downloads

        """
        Thread.__init__(self)
        self.sort = sort
        self.site = site
        self.title = title
        self.digits = digits
        self.reindex = reindex
        self.q = Queue()
        self.workers = []
        for i in range(workers):
            t = Thread(target=self.work_page)
            t.setDaemon(True)
            self.workers.append(t)
            t.start()
        self.index = JSONDict('./' + self.title + '/index.json', self.reindex)

    def get_chapters(self):
        my_str = self.site + '/' + self.title + '/'
        page = repeat_urlopen(my_str)
        soup = BeautifulSoup(page.read())
        links = soup.find_all('a')
        all_chapters = set()
        for l in links:
            try:
                if search(my_str + r'.*?c?([0-9]+(\.[0-9]+)?)/([0-9]+(\.html)?)?\Z', l['href']):
                    all_chapters.add(l['href'])
                elif search(self.title + r'.*?c?([0-9]+(\.[0-9]+)?)/([0-9]+(\.html)?)?\Z', l['href']):
                    # TODO: work on supporting mangapanda
                    all_chapters.add(self.site + l['href'])
            except:
                continue
        return all_chapters

    def work_chapter(self, my_str):
        try:
            search_str = self.site + '/' + self.title
            prefix = search(r'(.*?c?[0-9]+(\.[0-9]+)?/)([0-9]+(\.html)?)?\Z', my_str).group(1)
            ch_search = search(search_str + r'.*?c?([0-9]+(\.([0-9]+))?)/([0-9]+(\.html)?)?\Z', my_str)
            c = ch_search.group(1)
            if ch_search.group(2):
                c = int(ch_search.group(1)[:-len(ch_search.group(2))])
            else:
                c = int(c)
            self.padded_chapter = '0' * (self.digits - len(str(c))) + str(c)
            self.extra_chapter_add = 0
            self.extra_chapter = 0
            if ch_search.group(3):
                self.extra_chapter = int(ch_search.group(3))
                self.extra_chapter_add = 10**(self.digits-1) * self.extra_chapter
            if str(c) + '.' + str(self.extra_chapter) in self.index:
                return
            page = repeat_urlopen(my_str)
            # what about case when page is 404 or something?
            soup = BeautifulSoup(page.read())
            opts = soup.find_all('option')
            all_opts = set()
            opts_list = list()
            for o in opts:
                all_opts.add(Option(o.text, o.get("value")))
            for o in all_opts:
                s = search(search_str + r'.*?c?[0-9]+(\.([0-9]+))?/(([0-9]+)(\.html)?)?\Z', o.value)
                if not s:
                    if o.text and search(r'#?[0-9]+( / .*)?\Z', o.text) and search(r'\A[0-9]+\Z', o.value):
                        opts_list.append([prefix + o.value, o.value])
                    elif search(r'\A[0-9]+\Z', o.text) and search(r'\A/', o.value):
                        opts_list.append([self.site + o.value, o.text])
                elif s.group(3):
                    opts_list.append([o.value, s.group(4)])
                else:
                    opts_list.append([o.value, 1])
            for o in opts_list:
                self.q.put(o)
            self.q.join()
            self.index[str(c) + '.' + str(self.extra_chapter)] = True
        except:
            raise

    def work_page(self):
        while True:
            o = self.q.get()
            p = int(o[1]) + self.extra_chapter_add
            padded_page = '0' * (self.digits - len(str(p))) + str(p)
            filename = './' + self.title + '/' + self.padded_chapter + '.' + padded_page + '.jpg'
            ensure_dir(filename)
            if not isfile(filename) or getsize(filename) < 100:
                print(self.title, self.padded_chapter, padded_page)
                page = repeat_urlopen(o[0])
                if not page:
                    continue
                soup = BeautifulSoup(page.read())
                ids = ('image', 'picture')
                for i in ids:
                    image = soup.find('img',{'id':i})
                    if image:
                        break
                if not image:
                    continue
                image_url = image['src']
                if 'http://' not in image_url:
                     image_url = self.site + image_url
                with open(filename,'wb') as f:
                    f.write(repeat_urlopen(image_url).read())
            self.q.task_done()

    def sorted(self, c):
        if self.sort:
            return sorted(c)
        return c

    def run(self):
        print('**************** starting ' + self.title + ' ****************')
        chapters = self.get_chapters()
        for c in self.sorted(chapters):
            while True:
                try:
                    self.work_chapter(c)
                    break
                except urllib.error.HTTPError as e:
                    print('Error', e)
                    continue
        print('**************** ' + self.title + ' finished ****************')
