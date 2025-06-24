from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from fast_hotels import HotelData, Guests, get_hotels
from fast_flights import FlightData, Passengers, get_flights
import logging
import re
import pandas as pd
# from datetime import date as dt_date

# Helper functions for parsing price and stops
def _parse_price(price):
    if isinstance(price, str):
        # Remove any non-numeric characters except dot and comma
        match = re.search(r"[\d,.]+", price)
        if match:
            # Remove commas and convert to float
            return float(match.group(0).replace(",", ""))
        return None
    return price

def _parse_stops(stops):
    try:
        if stops is None:
            return None
        if isinstance(stops, int):
            return stops
        # Try to convert to int if it's a string representation of a number
        return int(stops)
    except (ValueError, TypeError):
        return None

app = FastAPI(title="Travel API", description="Plan your trip with the best flight and hotel options")

logging.basicConfig(level=logging.INFO)

# Hotel search models
class HotelSearchRequest(BaseModel):
    checkin_date: str = Field(..., description="Check-in date in YYYY-MM-DD format", examples=["2025-06-23"])
    checkout_date: str = Field(..., description="Check-out date in YYYY-MM-DD format", examples=["2025-06-25"])
    location: str = Field(..., description="City or location to search hotels in", examples=["Tokyo"])
    adults: int = Field(..., ge=1, description="Number of adult guests", examples=[2])
    children: int = Field(0, ge=0, description="Number of child guests", examples=[1])
    fetch_mode: str = Field("live", description="'live' for scraping, 'local' for mock data", examples=["live"])
    limit: int = Field(3, ge=1, description="Maximum number of hotel results to return", examples=[3])
    debug: bool = Field(False, description="Enable debug mode for scraping", examples=[False])

class HotelInfo(BaseModel):
    name: str = Field(..., description="Hotel name")
    price: Optional[float] = Field(None, description="Price per night in USD")
    rating: Optional[float] = Field(None, description="Hotel rating (e.g., 4.5)")
    url: Optional[str] = Field(None, description="URL to the hotel page")
    amenities: Optional[List[str]] = Field(None, description="List of hotel amenities")

class HotelSearchResponse(BaseModel):
    hotels: List[HotelInfo] = Field(..., description="List of hotel results")
    lowest_price: Optional[float] = Field(None, description="Lowest price among the results")
    current_price: Optional[float] = Field(None, description="Current price for the search")

# Flight search models
class FlightSearchRequest(BaseModel):
    date: str = Field(..., description="Flight date in YYYY-MM-DD format", examples=["2025-01-01"])
    from_airport: str = Field(..., description="IATA code of departure airport", examples=["TPE"])
    to_airport: str = Field(..., description="IATA code of arrival airport", examples=["MYJ"])
    trip: str = Field("one-way", description="Trip type: 'one-way' or 'round-trip'", examples=["one-way"])
    seat: str = Field("economy", description="Seat class: 'economy', 'business', etc.", examples=["economy"])
    adults: int = Field(..., ge=1, description="Number of adult passengers", examples=[2])
    children: int = Field(0, ge=0, description="Number of child passengers", examples=[1])
    infants_in_seat: int = Field(0, ge=0, description="Number of infants in seat", examples=[0])
    infants_on_lap: int = Field(0, ge=0, description="Number of infants on lap", examples=[0])
    fetch_mode: str = Field("fallback", description="Fetch mode: 'fallback', 'live', etc.", examples=["fallback"])

class FlightInfo(BaseModel):
    name: str = Field(..., description="Airline or flight name")
    departure: str = Field(..., description="Departure time and date")
    arrival: str = Field(..., description="Arrival time and date")
    arrival_time_ahead: Optional[str] = Field(None, description="Arrival time ahead (e.g., '+1' for next day)")
    duration: Optional[str] = Field(None, description="Flight duration (e.g., '4h 30m')")
    stops: Optional[int] = Field(None, description="Number of stops (0 for nonstop)")
    delay: Optional[str] = Field(None, description="Delay information if available")
    price: Optional[float] = Field(None, description="Flight price in USD")
    is_best: Optional[bool] = Field(None, description="Whether this is the best flight option")

class FlightSearchResponse(BaseModel):
    flights: List[FlightInfo] = Field(..., description="List of flight results")
    current_price: Optional[str] = Field(None, description="Current price for the search")

class HotelPreferences(BaseModel):
    star_rating: Optional[int] = Field(None, description="Minimum hotel star rating", examples=[3])
    max_price_per_night: Optional[float] = Field(None, description="Maximum price per night in USD", examples=[150])
    amenities: Optional[list[str]] = Field(None, description="Required hotel amenities", examples=[["Free Wi-Fi", "Breakfast ($)"]])

class TripPlanRequest(BaseModel):
    origin: str = Field(..., description="IATA code of origin airport", examples=["LHR"])
    destination: str = Field(..., description="IATA code of destination airport", examples=["CDG"])
    depart_date: str = Field(..., description="Departure date in YYYY-MM-DD format", examples=["2025-06-24"])
    return_date: Optional[str] = Field(None, description="Return date in YYYY-MM-DD format (optional)", examples=["2025-06-30"])
    adults: int = Field(..., ge=1, description="Number of adult travelers", examples=[1])
    children: int = Field(0, ge=0, description="Number of child travelers", examples=[0])
    hotel_preferences: Optional[HotelPreferences] = Field(None, description="Hotel preferences (optional)")
    max_total_budget: Optional[float] = Field(None, description="Maximum total budget for the trip in USD", examples=[3000])

    @field_validator('depart_date')
    def depart_date_not_in_past(cls, v):
        import datetime
        depart = datetime.datetime.strptime(v, "%Y-%m-%d").date()
        today = datetime.date.today()
        if depart < today:
            raise ValueError("depart_date cannot be in the past.")
        return v

class TripPlanResponse(BaseModel):
    best_outbound_flight: Optional[FlightInfo] = Field(None, description="Best outbound flight option")
    best_return_flight: Optional[FlightInfo] = Field(None, description="Best return flight option (if applicable)")
    best_hotel: Optional[HotelInfo] = Field(None, description="Best hotel option")
    total_estimated_cost: Optional[float] = Field(None, description="Total estimated cost for the trip in USD")
    per_person_per_day: Optional[float] = Field(None, description="Estimated cost per person per day in USD")
    breakdown: Dict[str, Any] = Field(..., description="Breakdown of costs and trip details")
    suggestions: Optional[str] = Field(None, description="Suggestions for optimizing the trip or saving money")

@app.post("/hotels/search", response_model=HotelSearchResponse)
def search_hotels(req: HotelSearchRequest):
    try:
        hotel_data = [HotelData(
            checkin_date=req.checkin_date,
            checkout_date=req.checkout_date,
            location=req.location
        )]
        guests = Guests(adults=req.adults, children=req.children)
        result = get_hotels(
            hotel_data=hotel_data,
            guests=guests,
            fetch_mode=req.fetch_mode,
            debug=req.debug,
            limit=req.limit
        )
        hotels = [HotelInfo(
            name=h.name,
            price=getattr(h, 'price', None),
            rating=getattr(h, 'rating', None),
            url=getattr(h, 'url', None),
            amenities=getattr(h, 'amenities', None)
        ) for h in result.hotels]
        return HotelSearchResponse(
            hotels=hotels,
            lowest_price=getattr(result, 'lowest_price', None),
            current_price=getattr(result, 'current_price', None)
        )
    except Exception as e:
        logging.error(f"Hotel search error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/flights/search", response_model=FlightSearchResponse)
def search_flights(req: FlightSearchRequest):
    try:
        flight_data = [FlightData(
            date=req.date,
            from_airport=req.from_airport,
            to_airport=req.to_airport
        )]
        passengers = Passengers(
            adults=req.adults,
            children=req.children,
            infants_in_seat=req.infants_in_seat,
            infants_on_lap=req.infants_on_lap
        )
        result = get_flights(
            flight_data=flight_data,
            trip=req.trip,
            seat=req.seat,
            passengers=passengers,
            fetch_mode=req.fetch_mode
        )
        flights = [FlightInfo(
            name=f.name,
            departure=f.departure,
            arrival=f.arrival,
            arrival_time_ahead=getattr(f, 'arrival_time_ahead', None),
            duration=getattr(f, 'duration', None),
            stops=_parse_stops(getattr(f, 'stops', None)),
            delay=getattr(f, 'delay', None),
            price=_parse_price(getattr(f, 'price', None)),
            is_best=getattr(f, 'is_best', None)
        ) for f in result.flights]
        return FlightSearchResponse(
            flights=flights,
            current_price=getattr(result, 'current_price', None)
        )
    except Exception as e:
        logging.error(f"Flight search error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/trip/plan", response_model=TripPlanResponse)
def plan_trip(req: TripPlanRequest):
    try:
        # Search for outbound flight (always one-way)
        try:
            passengers = Passengers(
                adults=req.adults,
                children=req.children,
                infants_in_seat=0,
                infants_on_lap=0
            )
            outbound_flight_data = [FlightData(
                date=req.depart_date,
                from_airport=req.origin,
                to_airport=req.destination
            )]
            outbound_result = get_flights(
                flight_data=outbound_flight_data,
                trip="one-way",
                seat="economy",
                passengers=passengers,
                fetch_mode="local"
            )
            outbound_flights = [
                FlightInfo(
                    name=f.name,
                    departure=f.departure,
                    arrival=f.arrival,
                    arrival_time_ahead=getattr(f, 'arrival_time_ahead', None),
                    duration=getattr(f, 'duration', None),
                    stops=_parse_stops(getattr(f, 'stops', None)),
                    delay=getattr(f, 'delay', None),
                    price=_parse_price(getattr(f, 'price', None)),
                    is_best=getattr(f, 'is_best', None)
                ) for f in outbound_result.flights
            ]
            best_outbound_flight = min((f for f in outbound_flights if f.price is not None), key=lambda x: x.price, default=None)
        except Exception as e:
            logging.error(f"Outbound flight search error in /trip/plan: {e}")
            raise HTTPException(status_code=502, detail=f"Outbound flight search failed: {e}")

        # Search for return flight using all trip types if return_date is provided
        best_return_flight = None
        if getattr(req, 'return_date', None):
            best_price = float('inf')
            for trip_type in ["one-way", "round-trip", "multi-city"]:
                try:
                    return_flight_data = [FlightData(
                        date=req.return_date,
                        from_airport=req.destination,
                        to_airport=req.origin
                    )]
                    return_result = get_flights(
                        flight_data=return_flight_data,
                        trip=trip_type,
                        seat="economy",
                        passengers=passengers,
                        fetch_mode="local"
                    )
                    return_flights = [
                        FlightInfo(
                            name=f.name,
                            departure=f.departure,
                            arrival=f.arrival,
                            arrival_time_ahead=getattr(f, 'arrival_time_ahead', None),
                            duration=getattr(f, 'duration', None),
                            stops=_parse_stops(getattr(f, 'stops', None)),
                            delay=getattr(f, 'delay', None),
                            price=_parse_price(getattr(f, 'price', None)),
                            is_best=getattr(f, 'is_best', None)
                        ) for f in return_result.flights
                    ]
                    candidate = min((f for f in return_flights if f.price is not None), key=lambda x: x.price, default=None)
                    if candidate and candidate.price is not None and candidate.price < best_price:
                        best_price = candidate.price
                        best_return_flight = candidate
                except Exception as e:
                    logging.warning(f"Return flight search error for trip_type {trip_type} in /trip/plan: {e}")
        # else: best_return_flight remains None

        # Search for hotels
        try:
            hotel_data = [HotelData(
                checkin_date=req.depart_date,
                checkout_date=req.return_date if getattr(req, 'return_date', None) else req.depart_date,
                location=req.destination
            )]
            guests = Guests(adults=req.adults, children=req.children)
            hotel_result = get_hotels(
                hotel_data=hotel_data,
                guests=guests,
                fetch_mode="live",
                debug=False,
                limit=10
            )
            hotels = [
                HotelInfo(
                    name=h.name,
                    price=getattr(h, 'price', None),
                    rating=getattr(h, 'rating', None),
                    url=getattr(h, 'url', None),
                    amenities=getattr(h, 'amenities', None)
                ) for h in hotel_result.hotels
            ]
            # Filter hotels by preferences
            filtered_hotels = hotels
            if req.hotel_preferences:
                if req.hotel_preferences.star_rating:
                    filtered_hotels = [h for h in filtered_hotels if h.rating and h.rating >= req.hotel_preferences.star_rating]
                if req.hotel_preferences.max_price_per_night:
                    filtered_hotels = [h for h in filtered_hotels if h.price and h.price <= req.hotel_preferences.max_price_per_night]
                if req.hotel_preferences.amenities:
                    filtered_hotels = [h for h in filtered_hotels if h.amenities and all(a in h.amenities for a in req.hotel_preferences.amenities)]
            best_hotel = min((h for h in filtered_hotels if h.price is not None), key=lambda x: x.price, default=None)
        except Exception as e:
            logging.error(f"Hotel search error in /trip/plan: {e}")
            raise HTTPException(status_code=502, detail=f"Hotel search failed: {e}")

        # Calculate total cost
        total_flight_cost = 0
        if best_outbound_flight and best_outbound_flight.price:
            total_flight_cost += best_outbound_flight.price * (req.adults + req.children)
        if best_return_flight and best_return_flight.price:
            total_flight_cost += best_return_flight.price * (req.adults + req.children)
        nights = (pd.to_datetime(req.return_date) - pd.to_datetime(req.depart_date)).days if getattr(req, 'return_date', None) else 1
        total_hotel_cost = (best_hotel.price * nights) if best_hotel and best_hotel.price else 0
        total_estimated_cost = total_flight_cost + total_hotel_cost
        per_person_per_day = total_estimated_cost / ((req.adults + req.children) * nights) if nights > 0 and (req.adults + req.children) > 0 else None

        breakdown = {
            "flight": total_flight_cost,
            "hotel": total_hotel_cost,
            "nights": nights,
            "adults": req.adults,
            "children": req.children
        }
        suggestions = None
        if req.max_total_budget and total_estimated_cost > req.max_total_budget:
            suggestions = "Consider adjusting your dates, reducing hotel star rating, or increasing your budget."

        return TripPlanResponse(
            best_outbound_flight=best_outbound_flight,
            best_return_flight=best_return_flight,
            best_hotel=best_hotel,
            total_estimated_cost=total_estimated_cost,
            per_person_per_day=per_person_per_day,
            breakdown=breakdown,
            suggestions=suggestions
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Trip plan error: {e}")
        raise HTTPException(status_code=400, detail=str(e)) 