from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
import bcrypt
import jwt
from enum import Enum

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# JWT Configuration
JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_TIME = timedelta(hours=24)

security = HTTPBearer()

# Enums
class UserRole(str, Enum):
    CUSTOMER = "customer"
    ADMIN = "admin"

class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

# Database Models
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    name: str
    role: UserRole = UserRole.CUSTOMER
    phone: Optional[str] = None
    address: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UserCreate(BaseModel):
    email: EmailStr
    name: str
    password: str
    phone: Optional[str] = None
    address: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Category(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    image_url: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class CategoryCreate(BaseModel):
    name: str
    description: str
    image_url: str

class Product(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    price: float
    category_id: str
    image_url: str
    stock_quantity: int
    organic_certification: bool = True
    farm_origin: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    category_id: str
    image_url: str
    stock_quantity: int
    organic_certification: bool = True
    farm_origin: Optional[str] = None

class CartItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    product_id: str
    quantity: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class CartItemCreate(BaseModel):
    product_id: str
    quantity: int

class OrderItem(BaseModel):
    product_id: str
    product_name: str
    price: float
    quantity: int

class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[OrderItem]
    total_amount: float
    status: OrderStatus = OrderStatus.PENDING
    delivery_address: str
    payment_method: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class OrderCreate(BaseModel):
    items: List[OrderItem]
    delivery_address: str
    payment_method: str

# Helper Functions
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_jwt_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + JWT_EXPIRATION_TIME
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        
        user = await db.users.find_one({"id": user_id})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        return User(**user)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

# Authentication Routes
@api_router.post("/auth/register", response_model=User)
async def register(user_data: UserCreate):
    # Check if user exists
    existing_user = await db.users.find_one({"email": user_data.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Hash password
    hashed_password = hash_password(user_data.password)
    
    # Create user
    user_dict = user_data.dict(exclude={"password"})
    user_obj = User(**user_dict)
    
    # Store user with hashed password
    user_with_password = user_obj.dict()
    user_with_password["password"] = hashed_password
    
    await db.users.insert_one(user_with_password)
    return user_obj

@api_router.post("/auth/login")
async def login(login_data: UserLogin):
    user = await db.users.find_one({"email": login_data.email})
    if not user or not verify_password(login_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_jwt_token(user["id"], user["email"], user["role"])
    user_obj = User(**user)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_obj
    }

@api_router.get("/auth/me", response_model=User)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    return current_user

# Category Routes
@api_router.get("/categories", response_model=List[Category])
async def get_categories():
    categories = await db.categories.find().to_list(100)
    return [Category(**category) for category in categories]

@api_router.post("/categories", response_model=Category)
async def create_category(category_data: CategoryCreate, admin: User = Depends(get_admin_user)):
    category_obj = Category(**category_data.dict())
    await db.categories.insert_one(category_obj.dict())
    return category_obj

# Product Routes
@api_router.get("/products", response_model=List[Product])
async def get_products(category_id: Optional[str] = None):
    filter_query = {}
    if category_id:
        filter_query["category_id"] = category_id
    
    products = await db.products.find(filter_query).to_list(100)
    return [Product(**product) for product in products]

@api_router.get("/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**product)

@api_router.post("/products", response_model=Product)
async def create_product(product_data: ProductCreate, admin: User = Depends(get_admin_user)):
    product_obj = Product(**product_data.dict())
    await db.products.insert_one(product_obj.dict())
    return product_obj

# Cart Routes
@api_router.get("/cart", response_model=List[CartItem])
async def get_cart_items(current_user: User = Depends(get_current_user)):
    cart_items = await db.cart_items.find({"user_id": current_user.id}).to_list(100)
    return [CartItem(**item) for item in cart_items]

@api_router.post("/cart", response_model=CartItem)
async def add_to_cart(item_data: CartItemCreate, current_user: User = Depends(get_current_user)):
    # Check if item already exists in cart
    existing_item = await db.cart_items.find_one({
        "user_id": current_user.id,
        "product_id": item_data.product_id
    })
    
    if existing_item:
        # Update quantity
        new_quantity = existing_item["quantity"] + item_data.quantity
        await db.cart_items.update_one(
            {"id": existing_item["id"]},
            {"$set": {"quantity": new_quantity}}
        )
        existing_item["quantity"] = new_quantity
        return CartItem(**existing_item)
    else:
        # Create new cart item
        cart_item_obj = CartItem(user_id=current_user.id, **item_data.dict())
        await db.cart_items.insert_one(cart_item_obj.dict())
        return cart_item_obj

@api_router.delete("/cart/{item_id}")
async def remove_from_cart(item_id: str, current_user: User = Depends(get_current_user)):
    result = await db.cart_items.delete_one({
        "id": item_id,
        "user_id": current_user.id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return {"message": "Item removed from cart"}

# Order Routes
@api_router.get("/orders", response_model=List[Order])
async def get_orders(current_user: User = Depends(get_current_user)):
    orders = await db.orders.find({"user_id": current_user.id}).to_list(100)
    return [Order(**order) for order in orders]

@api_router.post("/orders", response_model=Order)
async def create_order(order_data: OrderCreate, current_user: User = Depends(get_current_user)):
    # Calculate total amount
    total_amount = sum(item.price * item.quantity for item in order_data.items)
    
    order_obj = Order(
        user_id=current_user.id,
        total_amount=total_amount,
        **order_data.dict()
    )
    
    await db.orders.insert_one(order_obj.dict())
    
    # Clear cart after order
    await db.cart_items.delete_many({"user_id": current_user.id})
    
    return order_obj

@api_router.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str, current_user: User = Depends(get_current_user)):
    order = await db.orders.find_one({
        "id": order_id,
        "user_id": current_user.id
    })
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return Order(**order)

# Admin Routes
@api_router.get("/admin/orders", response_model=List[Order])
async def get_all_orders(admin: User = Depends(get_admin_user)):
    orders = await db.orders.find().to_list(100)
    return [Order(**order) for order in orders]

@api_router.put("/admin/orders/{order_id}/status")
async def update_order_status(order_id: str, status: OrderStatus, admin: User = Depends(get_admin_user)):
    result = await db.orders.update_one(
        {"id": order_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"message": "Order status updated"}

@api_router.get("/admin/users", response_model=List[User])
async def get_all_users(admin: User = Depends(get_admin_user)):
    users = await db.users.find({}, {"password": 0}).to_list(100)
    return [User(**user) for user in users]

# Initialize default categories and products
@api_router.post("/init-data")
async def initialize_data():
    # Check if categories already exist
    existing_categories = await db.categories.count_documents({})
    if existing_categories > 0:
        return {"message": "Data already initialized"}
    
    # Default categories
    categories_data = [
        {
            "name": "Fresh Vegetables",
            "description": "Organic vegetables freshly harvested from local farms",
            "image_url": "https://images.unsplash.com/photo-1540420773420-3366772f4999"
        },
        {
            "name": "Fresh Fruits",
            "description": "Seasonal organic fruits packed with natural goodness",
            "image_url": "https://images.unsplash.com/photo-1598471338675-f3c09a43cda1"
        },
        {
            "name": "Dairy Products",
            "description": "Fresh dairy products from grass-fed organic farms",
            "image_url": "https://images.unsplash.com/photo-1634141510639-d691d86f47de"
        },
        {
            "name": "Grains & Cereals",
            "description": "Wholesome organic grains and cereals",
            "image_url": "https://images.unsplash.com/photo-1562437243-4117943e59b8"
        }
    ]
    
    # Insert categories
    for cat_data in categories_data:
        category_obj = Category(**cat_data)
        await db.categories.insert_one(category_obj.dict())
    
    # Get categories for product creation
    categories = await db.categories.find().to_list(100)
    veg_cat = next(cat for cat in categories if cat["name"] == "Fresh Vegetables")
    fruit_cat = next(cat for cat in categories if cat["name"] == "Fresh Fruits")
    dairy_cat = next(cat for cat in categories if cat["name"] == "Dairy Products")
    
    # Get grains category for additional products
    grains_cat = next(cat for cat in categories if cat["name"] == "Grains & Cereals")
    
    # Sample products with INR pricing and Indian organic products
    products_data = [
        # Vegetables
        {
            "name": "Organic Spinach (Palak)",
            "description": "Fresh organic spinach leaves rich in iron and vitamins",
            "price": 80.0,
            "category_id": veg_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1576045057995-568f588f82fb",
            "stock_quantity": 50,
            "farm_origin": "Green Valley Farm, Punjab"
        },
        {
            "name": "Organic Carrots (Gajar)",
            "description": "Sweet and crunchy organic carrots",
            "price": 120.0,
            "category_id": veg_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1582515073490-39981397c445",
            "stock_quantity": 75,
            "farm_origin": "Himalayan Organic Farm"
        },
        {
            "name": "Organic Tomatoes",
            "description": "Fresh organic tomatoes perfect for cooking",
            "price": 90.0,
            "category_id": veg_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1546470427-e212b9d65c8c",
            "stock_quantity": 60,
            "farm_origin": "Nashik Organic Valley"
        },
        {
            "name": "Organic Onions (Pyaaz)",
            "description": "Chemical-free red onions from certified organic farms",
            "price": 70.0,
            "category_id": veg_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1518977822534-7049a61b1936",
            "stock_quantity": 100,
            "farm_origin": "Maharashtra Organic Farms"
        },
        {
            "name": "Organic Cauliflower (Gobhi)",
            "description": "Fresh organic cauliflower grown without pesticides",
            "price": 85.0,
            "category_id": veg_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1568584711271-95c67199f895",
            "stock_quantity": 40,
            "farm_origin": "Punjab Natural Farms"
        },
        
        # Fruits
        {
            "name": "Organic Strawberries",
            "description": "Juicy organic strawberries picked at peak ripeness",
            "price": 450.0,
            "category_id": fruit_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1598471338675-f3c09a43cda1",
            "stock_quantity": 30,
            "farm_origin": "Himachal Berry Farms"
        },
        {
            "name": "Organic Apples (Seb)",
            "description": "Crisp organic apples from Kashmir valley",
            "price": 280.0,
            "category_id": fruit_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1560806887-1e4cd0b6cbd6",
            "stock_quantity": 80,
            "farm_origin": "Kashmir Organic Orchards"
        },
        {
            "name": "Organic Bananas (Kela)",
            "description": "Sweet organic bananas rich in potassium",
            "price": 60.0,
            "category_id": fruit_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1571771894821-ce9b6c11b08e",
            "stock_quantity": 120,
            "farm_origin": "Kerala Organic Plantations"
        },
        {
            "name": "Organic Mangoes (Aam)",
            "description": "King of fruits - organic Alphonso mangoes",
            "price": 320.0,
            "category_id": fruit_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1605664515728-4e747d50c4c9",
            "stock_quantity": 45,
            "farm_origin": "Ratnagiri Organic Farms"
        },
        
        # Dairy Products
        {
            "name": "Organic Milk (Doodh)",
            "description": "Fresh organic whole milk from grass-fed desi cows",
            "price": 80.0,
            "category_id": dairy_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1634141510639-d691d86f47de",
            "stock_quantity": 25,
            "farm_origin": "Gir Cow Dairy, Gujarat"
        },
        {
            "name": "Organic Ghee",
            "description": "Pure organic cow ghee made using traditional methods",
            "price": 650.0,
            "category_id": dairy_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1628088062854-d1870b4553da",
            "stock_quantity": 35,
            "farm_origin": "Vrindavan Organic Dairy"
        },
        {
            "name": "Organic Paneer",
            "description": "Fresh organic cottage cheese made from pure milk",
            "price": 180.0,
            "category_id": dairy_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1631452180519-c014fe946bc7",
            "stock_quantity": 20,
            "farm_origin": "Amul Organic"
        },
        
        # Grains & Cereals
        {
            "name": "Organic Basmati Rice",
            "description": "Premium organic basmati rice with authentic aroma",
            "price": 220.0,
            "category_id": grains_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1586201375761-83865001e31c",
            "stock_quantity": 100,
            "farm_origin": "Haryana Organic Mills"
        },
        {
            "name": "Organic Wheat Flour (Atta)",
            "description": "Stone-ground organic wheat flour for healthy rotis",
            "price": 85.0,
            "category_id": grains_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b",
            "stock_quantity": 150,
            "farm_origin": "Punjab Organic Farms"
        },
        {
            "name": "Organic Toor Dal",
            "description": "Premium organic yellow lentils rich in protein",
            "price": 140.0,
            "category_id": grains_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1596797038530-2c107229654b",
            "stock_quantity": 80,
            "farm_origin": "Rajasthan Organic Co-op"
        },
        {
            "name": "Organic Moong Dal",
            "description": "Organic green gram dal perfect for daily meals",
            "price": 160.0,
            "category_id": grains_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1596797038530-2c107229654b",
            "stock_quantity": 70,
            "farm_origin": "Maharashtra Organic Mills"
        },
        {
            "name": "Organic Quinoa",
            "description": "Superfood organic quinoa rich in protein and fiber",
            "price": 380.0,
            "category_id": grains_cat["id"],
            "image_url": "https://images.unsplash.com/photo-1586444248902-2f64eddc13df",
            "stock_quantity": 40,
            "farm_origin": "Himalayan Organic Farms"
        }
    ]
    
    # Insert products
    for prod_data in products_data:
        product_obj = Product(**prod_data)
        await db.products.insert_one(product_obj.dict())
    
    # Create admin user
    admin_data = {
        "email": "admin@organicfood.com",
        "name": "System Admin",
        "password": hash_password("admin123"),
        "role": UserRole.ADMIN
    }
    admin_obj = User(email=admin_data["email"], name=admin_data["name"], role=admin_data["role"])
    admin_with_password = admin_obj.dict()
    admin_with_password["password"] = admin_data["password"]
    await db.users.insert_one(admin_with_password)
    
    return {"message": "Initial data created successfully"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
from fastapi.staticfiles import StaticFiles

# Serve the React build folder
app.mount("/", StaticFiles(directory="../frontend/build", html=True), name="frontend")
