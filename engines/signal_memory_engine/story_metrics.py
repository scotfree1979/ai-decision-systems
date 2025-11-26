saved_chapter_counter = 0
skipped_chapter_counter = 0

def increment_saved_chapters():
    global saved_chapter_counter
    saved_chapter_counter += 1

def increment_skipped_chapters():
    global skipped_chapter_counter
    skipped_chapter_counter += 1

def print_summary():
    print(f"\n✅ Saved {saved_chapter_counter} chapters")
    print(f"⚠️ Skipped {skipped_chapter_counter} due to missing or invalid marketStartTime")
