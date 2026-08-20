#!/usr/bin/env bash
# =============================================================================
# Production Startup Script for Web Scraper System
# =============================================================================
# This script provides multiple ways to run the scraper:
#   ./start.sh worker          - Run Celery worker (default)
#   ./start.sh beat            - Run Celery beat scheduler
#   ./start.sh flower          - Run Flower monitoring UI
#   ./start.sh csv             - Run CSV processor directly (no Celery)
#   ./start.sh csv-once        - Run CSV processor once and exit
#   ./start.sh status          - Show checkpoint/status
#   ./start.sh health          - Health check
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

check_env() {
    if [[ ! -f .env ]]; then
        log_warning ".env file not found, copying from .env.example"
        cp .env.example .env
    fi
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi
}

cmd_worker() {
    log_info "Starting Celery worker..."
    docker-compose up -d redis
    docker-compose up worker
}

cmd_beat() {
    log_info "Starting Celery beat scheduler..."
    docker-compose up -d redis
    docker-compose up beat
}

cmd_flower() {
    log_info "Starting Flower monitoring UI..."
    docker-compose up -d redis
    docker-compose up flower
}

cmd_csv() {
    log_info "Running CSV processor (continuous)..."
    docker-compose run --rm worker python -m scraper.csv_processor
}

cmd_csv_once() {
    log_info "Running CSV processor (single run)..."
    docker-compose run --rm worker python scripts/process_companydata.py
}

cmd_test() {
    log_info "Running TEST scrape (limited to 10 companies)..."
    docker-compose -f docker-compose.yml -f docker-compose.test.yml up --build --abort-on-container-exit
}

cmd_status() {
    log_info "Checking checkpoint status..."
    docker-compose run --rm worker python -c "
from scraper.tasks import checkpoint_status
import json
result = checkpoint_status.delay()
status = result.get(timeout=30)
print(json.dumps(status, indent=2))
"
}

cmd_health() {
    log_info "Running health check..."
    docker-compose run --rm worker python -c "
from scraper.tasks import health_check
import json
result = health_check.delay()
status = result.get(timeout=30)
print(json.dumps(status, indent=2))
"
}

cmd_all() {
    log_info "Starting all services (worker, beat, flower)..."
    docker-compose up -d
    log_success "All services started. Flower UI: http://localhost:5555"
    log_info "View logs: docker-compose logs -f"
}

cmd_logs() {
    local service="${1:-}"
    if [[ -n "$service" ]]; then
        docker-compose logs -f "$service"
    else
        docker-compose logs -f
    fi
}

cmd_stop() {
    log_info "Stopping all services..."
    docker-compose down
    log_success "All services stopped"
}

cmd_restart() {
    log_info "Restarting all services..."
    docker-compose down
    docker-compose up -d
    log_success "All services restarted"
}

cmd_build() {
    log_info "Building Docker images..."
    docker-compose build --no-cache
    log_success "Build complete"
}

cmd_clean() {
    log_warning "This will remove all containers, volumes, and data!"
    read -p "Are you sure? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker-compose down -v --remove-orphans
        docker system prune -f
        log_success "Cleanup complete"
    else
        log_info "Cleanup cancelled"
    fi
}

show_help() {
    cat << EOF
Usage: ./start.sh <command> [args]

Commands:
  worker              Run Celery worker (default)
  beat                Run Celery beat scheduler
  flower              Run Flower monitoring UI (port 5555)
  csv                 Run CSV processor continuously (direct, no Celery)
  csv-once            Run CSV processor once and exit
  test                Run TEST scrape (limited to 10 companies via docker-compose.test.yml)
  status              Show checkpoint/resume status
  health              Run health check
  all                 Start all services (worker + beat + flower)
  logs [service]      View logs (optionally for specific service)
  stop                Stop all services
  restart             Restart all services
  build               Build Docker images
  clean               Remove all containers, volumes, and data (DANGEROUS)
  help                Show this help

Examples:
  ./start.sh                    # Run worker (default)
  ./start.sh all                # Start all services
  ./start.sh test               # Quick test: scrape 10 companies
  ./start.sh csv-once           # Process all CSVs once
  ./start.sh status             # Check progress
  ./start.sh logs worker        # View worker logs

Environment:
  Copy .env.example to .env and configure:
    - REDIS_URL
    - PROXY_LIST_PATH or PROXY_LIST
    - PRIORITY_CSV_FILES
    - MAX_CONCURRENT_BROWSERS
    - CSV_PROCESSING_CONCURRENCY
    - CHECKPOINT_INTERVAL
    - LOG_LEVEL
    - TEST_LIMIT=10 (for test runs)

Data Persistence:
  - Scraped data: ./data/json/ and ./data/csv/
  - Checkpoints: ./data/checkpoints/
  - Logs: ./logs/
EOF
}

# Main
main() {
    check_env
    
    local cmd="${1:-worker}"
    shift || true
    
    case "$cmd" in
        worker)     cmd_worker "$@" ;;
        beat)       cmd_beat "$@" ;;
        flower)     cmd_flower "$@" ;;
        csv)        cmd_csv "$@" ;;
        csv-once)   cmd_csv_once "$@" ;;
        test)       cmd_test "$@" ;;
        status)     cmd_status "$@" ;;
        health)     cmd_health "$@" ;;
        all)        cmd_all "$@" ;;
        logs)       cmd_logs "$@" ;;
        stop)       cmd_stop "$@" ;;
        restart)    cmd_restart "$@" ;;
        build)      cmd_build "$@" ;;
        clean)      cmd_clean "$@" ;;
        help|--help|-h) show_help ;;
        *)          log_error "Unknown command: $cmd"; show_help; exit 1 ;;
    esac
}

main "$@"