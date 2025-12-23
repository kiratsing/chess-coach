import berserk
import chess
import chess.pgn
import chess.engine
import io
import requests
# --- ADD THIS IMPORT at the top of your script ---
from collections import defaultdict
import psycopg2
from psycopg2 import sql
import json
import hashlib # Add this import

# --- DB CONFIGURATION ---
DB_NAME = "chess_analysis"
DB_USER = "kirat" 
DB_PASSWORD = None
DB_HOST = None
# ------------------------

def get_db_connection():
    """Establishes and returns a PostgreSQL connection using Peer Auth."""
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            # Note: We are explicitly NOT passing host or password here
        )
        return conn
    except psycopg2.Error as e:
        print(f"Database connection error: {e}")
        return None

# --- CONFIGURATION (unchanged from last step) ---
USERNAME = 'hunterisbad1' 
NUM_GAMES = 1
STOCKFISH_PATH = '/usr/games/stockfish' 
ANALYSIS_DEPTH = 18 
# ---------------------

def fetch_chessdotcom_games(username, max_games):
    """Fetches games from Chess.com API, streaming PGN data."""
    all_pgn_data = []
    
    # 1. DEFINE HEADERS TO AVOID 403 ERROR
    headers = {
        # This tells the server the request is coming from a known client (your app)
        'User-Agent': 'ChessCoachProject (YourAppName/1.0; contact@example.com)' 
    }
    
    # 2. Get list of archive URLs
    archive_url = f"https://api.chess.com/pub/player/{username}/games/archives"
    print(f"Checking Chess.com archives for {username}...")
    
    try:
        # PASS HEADERS HERE
        response = requests.get(archive_url, headers=headers)
        response.raise_for_status() 
        archives = response.json().get('archives', [])
    except requests.exceptions.RequestException as e:
        # Check if the error is due to a 404 (user not found) or 403 (access denied)
        if response.status_code == 404:
             print("Error: Username not found (404). Please check spelling.")
        elif response.status_code == 403:
             print("Error: Access Forbidden (403). Try adding a User-Agent header.")
        else:
             print(f"Error fetching archives: {e}")
        return []

    # Process archives from most recent (end of list) backwards
    for url in reversed(archives):
        print(f"Fetching games from {url}...")
        
        try:
            # 3. Fetch all games from the monthly archive URL
            # PASS HEADERS HERE AGAIN
            archive_response = requests.get(url, headers=headers)
            archive_response.raise_for_status()
            
            games_json = archive_response.json().get('games', [])
            
            for game_data in reversed(games_json):
                pgn_text = game_data.get('pgn')
                
                if pgn_text and len(all_pgn_data) < max_games:
                    all_pgn_data.append(pgn_text)
                    
                if len(all_pgn_data) >= max_games:
                    return all_pgn_data
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching archive {url}: {e}")
            continue

    return all_pgn_data

def get_mistake_category(cpl):
    """Categorizes the CPL into human-readable terms."""
    if cpl > 300:
        return "Blunder"
    elif cpl > 100:
        return "Mistake"
    elif cpl > 50:
        return "Inaccuracy"
    else:
        return "Good Move"

def get_game_phase(move_num):
    """Determines the phase of the game based on the move number."""
    if move_num <= 10:
        return "Opening"
    elif move_num <= 30:
        return "Middlegame"
    else:
        return "Endgame"
    
# --- (Keep get_centipawn_loss function here - It is unchanged) ---
def get_centipawn_loss(engine, board, move):
    """
    Calculates the CPL for a given move using the best practice of
    comparing the value of the engine's best move to the actual move.
    """
    
    # 1. Evaluate the position *before* the move to find the BEST possible score
    # We use a deeper search here to ensure we find the true best move evaluation
    info_best = engine.analyse(board, chess.engine.Limit(depth=18)) # Use your set ANALYSIS_DEPTH
    score_best = score_to_cp(info_best['score'].relative, mate_value=10000)
    
    # 2. Evaluate the position *after* the user's actual move
    board.push(move)
    info_actual = engine.analyse(board, chess.engine.Limit(depth=18))
    score_actual = score_to_cp(info_actual['score'].relative, mate_value=10000)
    board.pop()
    
    # CPL is the difference between the best possible evaluation and the evaluation after the move.
    # The result is capped at 0 (meaning no negative CPL/no "gain" is possible).
    cpl = score_best - score_actual
    
    return max(0, cpl)

def score_to_cp(score, mate_value=10000):
    """
    Converts a chess.engine.Score object (Cp or Mate) to a raw Centipawn integer.
    Mate scores are mapped to a large, fixed value (mate_value) adjusted for length.
    """
    if score.is_mate():
        # Mate in N moves. Positive for winning, negative for losing.
        mate_in = score.mate()
        if mate_in > 0:
            # Winning mate: (mate_value - N)
            return mate_value - mate_in
        else:
            # Losing mate: -(mate_value + N) where N is a positive number
            return -(mate_value + abs(mate_in))
    else:
        # Standard Centipawn score
        return score.cp

def analyze_game(game_pgn, engine):
    """Processes one game and calculates CPL for every user move, tagging it."""
    pgn_io = io.StringIO(game_pgn)
    game = chess.pgn.read_game(pgn_io)
    board = game.board()
    
    analysis_data = {
        'moves_analyzed': 0,
        'mistakes_by_phase': defaultdict(int), # Stores count of Blunders/Mistakes per phase
        'phase_cpl_sum': defaultdict(lambda: {'cpl': 0, 'count': 0}),
        'user_cpl_values': [] # Stores raw CPL for overall ACPL calculation
    }
    
    # ply() gives the half-move number (1. e4 is ply 1, 1...e5 is ply 2)
    move_count = 0 

    for move in game.mainline_moves():
        move_count += 1
        user_turn = (board.turn == chess.WHITE and game.headers["White"] == USERNAME) or \
                    (board.turn == chess.BLACK and game.headers["Black"] == USERNAME)

        if user_turn:
            cpl = get_centipawn_loss(engine, board, move)
            
            analysis_data['user_cpl_values'].append(cpl)
            analysis_data['moves_analyzed'] += 1
            
            # --- NEW TAGGING LOGIC ---
            category = get_mistake_category(cpl)
            phase = get_game_phase(move_count)
            
            if category != "Good Move":
                # Count the total number of mistakes/blunders in this phase
                analysis_data['mistakes_by_phase'][f"{phase} - {category}"] += 1
            
            # Sum CPL for calculating ACPL per phase
            analysis_data['phase_cpl_sum'][phase]['cpl'] += cpl
            analysis_data['phase_cpl_sum'][phase]['count'] += 1

        board.push(move)
        
    return analysis_data


def aggregate_all_games(all_results):
    """Combines stats from multiple games into a single summary dictionary."""
    if not all_results:
        return None

    final_summary = {
        'total_user_cpl_values': [],
        'total_moves_analyzed': 0,
        'mistakes_by_phase': defaultdict(int),
        'phase_cpl_sum': defaultdict(lambda: {'cpl': 0, 'count': 0})
    }

    for data in all_results:
        # Combine CPL lists for overall ACPL
        final_summary['total_user_cpl_values'].extend(data['user_cpl_values'])
        final_summary['total_moves_analyzed'] += data['moves_analyzed']

        # Sum up mistake counts across all games
        for error_type, count in data['mistakes_by_phase'].items():
            final_summary['mistakes_by_phase'][error_type] += count
            
        # Sum up CPL and count for ACPL per phase
        for phase, stats in data['phase_cpl_sum'].items():
            final_summary['phase_cpl_sum'][phase]['cpl'] += stats['cpl']
            final_summary['phase_cpl_sum'][phase]['count'] += stats['count']

    return final_summary



def save_game_analysis(conn, game_pgn, analysis_data):
    """Saves a single game's analysis results to the database."""
    
    # 1. Parse game header data for required fields (URL, Date)
    pgn_io = io.StringIO(game_pgn)
    game = chess.pgn.read_game(pgn_io)
    
    # --- START FIX: Use stable SHA-256 hashing for the unique ID ---
    game_url = game.headers.get("URL") # First, try to get the actual URL

    if not game_url:
        # If the URL header is missing, generate a consistent ID from the PGN content
        pgn_bytes = game_pgn.encode('utf-8')
        game_hash = hashlib.sha256(pgn_bytes).hexdigest()
        game_url = "SHA256_" + game_hash
    # --- END FIX ---
    
    game_date = game.headers.get("Date", "1970.01.01")
    
    # 2. Calculate ACPL for each phase from the analysis_data
    # ... (rest of the helper functions and ACPL calculations are unchanged)
    
    # 3. Use the JSONB field for the detailed mistake counts
    mistake_json = json.dumps(dict(analysis_data['mistakes_by_phase']))

    cursor = conn.cursor()
    
    insert_query = """
    INSERT INTO analyzed_games (game_url, username, date, pgn, overall_acpl, opening_acpl, middlegame_acpl, endgame_acpl, mistake_breakdown)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (game_url) DO UPDATE SET
        overall_acpl = EXCLUDED.overall_acpl,
        opening_acpl = EXCLUDED.opening_acpl,
        middlegame_acpl = EXCLUDED.middlegame_acpl,
        endgame_acpl = EXCLUDED.endgame_acpl,
        mistake_breakdown = EXCLUDED.mistake_breakdown;
    """
    
    try:
        cursor.execute(insert_query, (
            game_url, # Now this is a consistent ID!
            USERNAME, # Using the global username from config
            game_date,
            game_pgn,
            overall_acpl,
            opening_acpl,
            middlegame_acpl,
            endgame_acpl,
            mistake_json
        ))
        conn.commit()
    except Exception as e:
        print(f"Error inserting game {game_url}: {e}")
        conn.rollback()
    finally:
        cursor.close()
        
def generate_report(aggregated_data):
    """Generates the coaching report based on aggregated analysis."""
    
    if not aggregated_data:
        print("No aggregated data to report.")
        return

    user_cpl_values = aggregated_data['total_user_cpl_values']
    
    if not user_cpl_values:
        print("No moves were analyzed for the user across all games.")
        return

    # 1. Overall Summary
    avg_cpl = sum(user_cpl_values) / len(user_cpl_values)
    print("\n\n--- ♟️ COACHING REPORT (10 Games) ---")
    print(f"Overall Accuracy Score (ACPL): **{avg_cpl:.2f} cp** (This places you in the Beginner range >100 cp)")
    
    # 2. Phase Analysis
    print("\n### Weakness by Game Phase")
    
    # Find the phase with the highest ACPL
    highest_cpl_phase = None
    max_acpl = -1
    
    phase_stats = aggregated_data['phase_cpl_sum']
    
    for phase, stats in phase_stats.items():
        if stats['count'] > 0:
            phase_acpl = stats['cpl'] / stats['count']
            print(f"- {phase} ACPL: {phase_acpl:.2f} cp ({stats['count']} moves)")
            
            if phase_acpl > max_acpl:
                max_acpl = phase_acpl
                highest_cpl_phase = phase
    
    if highest_cpl_phase:
        print(f"\n💡 **Primary Focus:** Your highest error rate occurred in the **{highest_cpl_phase}** phase.")
    
    # 3. Mistake Count Summary
    print("\n### Mistake Breakdown")
    total_mistakes = 0
    
    # Find the most common specific mistake type
    mistake_counts = aggregated_data['mistakes_by_phase']
    
    if mistake_counts:
        most_common_error = max(mistake_counts.items(), key=lambda item: item[1], default=("None", 0))
        
        if most_common_error[1] > 0:
            print(f"**Actionable Insight:** The most frequent error was a **{most_common_error[0]}**.")
            print("---")
            for error, count in mistake_counts.items():
                total_mistakes += count
                print(f"- {error}: {count} times")
            print(f"\n**Total Blunders/Mistakes/Inaccuracies:** {total_mistakes}")

def is_game_analyzed(conn, game_pgn):
    """Checks the database to see if a game (by URL) has already been analyzed."""
    
    # Extract the unique URL from the PGN header
    pgn_io = io.StringIO(game_pgn)
    game = chess.pgn.read_game(pgn_io)

    # --- SIMPLIFIED AND ENFORCED CONSISTENT ID GENERATION ---
    # NEW CODE (Guarantees consistency)
    game_url = game.headers.get("URL")
    
    if not game_url:
        # If the URL header is missing (as is common with Chess.com API PGNs)
        # Use SHA-256 hash of the PGN text to create a unique, consistent ID
        pgn_bytes = game_pgn.encode('utf-8')
        game_hash = hashlib.sha256(pgn_bytes).hexdigest()
        game_url = "SHA256_" + game_hash
    # --- END CONSISTENT ID GENERATION ---
    
    cursor = conn.cursor()
    # ... (rest of function remains the same)
    
    cursor = conn.cursor()
    
    # Use SELECT EXISTS for the fastest possible check (returns True/False)
    query = sql.SQL("SELECT EXISTS(SELECT 1 FROM analyzed_games WHERE game_url = %s);")
    
    try:
        cursor.execute(query, (game_url,))
        # fetchone()[0] gets the boolean result of the EXISTS query
        already_analyzed = cursor.fetchone()[0] 
        return already_analyzed
    except Exception as e:
        # If the table doesn't exist or there's an error, assume it's not analyzed
        print(f"Database check error: {e}. Assuming unanalyzed.")
        return False
    finally:
        cursor.close()

def main():
    # --- MAIN EXECUTION (Updated for DB Skip Logic) ---
    conn = None  # Initialize outside try
    engine = None  # Initialize outside try

    try:
        # 1. Database Connection
        conn = get_db_connection()
        if conn is None:
            print("Fatal: Could not connect to database. Aborting analysis.")
            return

        # 2. Stockfish Engine Initialization
        print(f"Initializing Stockfish at depth {ANALYSIS_DEPTH}...")
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)

        # 3. Data Fetching
        print(f"Fetching last {NUM_GAMES} games for {USERNAME} from Chess.com...")
        games_pgn_list = fetch_chessdotcom_games(USERNAME, NUM_GAMES)
        
        if not games_pgn_list:
            print("Could not fetch any games from Chess.com. Aborting analysis.")
            return

        # 4. Filter Games: Implement DB Skip Logic
        games_to_analyze = []
        print("Checking database for analyzed games...", end="", flush=True)
        
        for game_pgn in games_pgn_list:
            if conn and is_game_analyzed(conn, game_pgn):
                print(".", end="", flush=True) # Print a dot for each skipped game
            else:
                games_to_analyze.append(game_pgn)

        print(f"\nFound {len(games_to_analyze)} new game(s) needing analysis.")
        
        # 5. Analysis and Saving Loop
        all_cpl_results = []
        
        for i, game_pgn in enumerate(games_to_analyze):
            print(f"Analyzing new game {i+1}/{len(games_to_analyze)}...")
            
            # ANALYSIS
            result = analyze_game(game_pgn, engine)
            all_cpl_results.append(result)
            
            # DATABASE SAVE
            save_game_analysis(conn, game_pgn, result)

        # 6. Final Report Generation
        # Aggregate ALL fetched games (analyzed and skipped) for a comprehensive report.
        # Note: If no new games were analyzed, we still aggregate the old ones from the DB 
        # for reporting, but for simplicity here, we only use the newly analyzed results.
        
        final_summary = aggregate_all_games(all_cpl_results)
        
        if final_summary:
            generate_report(final_summary)
        else:
            print("No new analysis was performed, and no data was available for reporting.")

    except Exception as e:
        print(f"\nAN ERROR OCCURRED: {e}")

    finally:
        # close engine and DB connection if they were opened
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
                print("Database connection closed.")
            except Exception:
                pass

if __name__ == "__main__":
    main()